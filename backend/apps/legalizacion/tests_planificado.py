"""Lo planificado, al lado de lo declarado, en la cola de aprobación.

Quien aprueba ve hoy «8 h — Integración Oracle» y no tiene con qué contrastarlo.
Si la asignación decía 4,3 h/día en esa misma tarea, eso es justo el contexto que
convierte una firma automática en una pregunta.

Lo que se cuida aquí, además del caso normal:

- que no se dispare una consulta por renglón, porque esta cola llega a cien;
- que una tarea de otro proyecto o de otra fecha no se cuele en el renglón
  equivocado, que sería peor que no mostrar nada.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.legalizacion import services as svc
from apps.legalizacion.models import DiaLegalizado, RegistroHoras, TipoActividad


class PlanificadoEnLaColaTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm_plan", "pm@test.com", "x")
        self.proyecto = Proyecto.objects.create(
            codigo="V-25188808/Q", nombre="Simulador", cliente="ANECOOP",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        self.otro = Proyecto.objects.create(
            codigo="V-25188809/Q", nombre="Otro", cliente="ANECOOP",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        self.recurso = Recurso.objects.create(
            nombre="Medina-Novoa Martin", email="martin@test.com", banda="SSR",
        )
        self.tipo = TipoActividad.objects.create(
            nombre="Desarrollo", requiere_proyecto=True,
        )
        self.fecha = date(2026, 9, 14)

    def _asignacion(self, proyecto, actividad, estado="APROBADA",
                    inicio=None, fin=None):
        return Asignacion.objects.create(
            recurso=self.recurso, proyecto=proyecto, actividad=actividad,
            modo_asignacion="RANGO",
            fecha_inicio=inicio or self.fecha, fecha_fin=fin or self.fecha,
            dias_habiles=1, horas_totales=5, intensidad_diaria=Decimal("4.3"),
            estado=estado, solicitada_por=self.pm,
        )

    def _registro(self, proyecto=None, horas="8.0", detalle="Ajustes al conector"):
        dia, _ = DiaLegalizado.objects.get_or_create(
            recurso=self.recurso, fecha=self.fecha,
            defaults={"estado": DiaLegalizado.REGISTRADO,
                      "total_horas": Decimal("8.5"),
                      "jornada_esperada": Decimal("8.5")},
        )
        dia.estado = DiaLegalizado.REGISTRADO
        dia.save(update_fields=["estado"])
        return RegistroHoras.objects.create(
            dia=dia, tipo_actividad=self.tipo,
            proyecto=proyecto or self.proyecto,
            horas=Decimal(horas), detalle=detalle,
        )

    def test_muestra_la_tarea_planificada_de_ese_dia_y_proyecto(self):
        self._asignacion(self.proyecto, "Integración Oracle")
        self._registro()

        dia = svc.dias_por_aprobar(self.pm)[0]
        self.assertEqual(dia.pendientes_mios[0].planificado, ["Integración Oracle"])

    def test_una_asignacion_solicitada_se_marca_como_tal(self):
        """Un cronograma recien cargado esta entero en SOLICITADA: esconderlo
        lo dejaria sin uso justo cuando mas hace falta."""
        self._asignacion(self.proyecto, "Integración Oracle", estado="SOLICITADA")
        self._registro()

        dia = svc.dias_por_aprobar(self.pm)[0]
        self.assertEqual(
            dia.pendientes_mios[0].planificado, ["Integración Oracle (solicitada)"],
        )

    def test_dos_tareas_del_mismo_proyecto_el_mismo_dia(self):
        """Pasa en los cronogramas reales: Martin tenia dos tareas el 07/09."""
        self._asignacion(self.proyecto, "Integración Oracle")
        self._asignacion(self.proyecto, "Selector de campaña")
        self._registro()

        dia = svc.dias_por_aprobar(self.pm)[0]
        self.assertCountEqual(
            dia.pendientes_mios[0].planificado,
            ["Integración Oracle", "Selector de campaña"],
        )

    def test_no_se_cuela_la_tarea_de_otro_proyecto(self):
        self._asignacion(self.otro, "Tarea del otro proyecto")
        self._registro()

        dia = svc.dias_por_aprobar(self.pm)[0]
        self.assertEqual(dia.pendientes_mios[0].planificado, [])

    def test_no_se_cuela_la_tarea_de_otra_fecha(self):
        self._asignacion(
            self.proyecto, "Tarea de la semana pasada",
            inicio=date(2026, 9, 7), fin=date(2026, 9, 8),
        )
        self._registro()

        dia = svc.dias_por_aprobar(self.pm)[0]
        self.assertEqual(dia.pendientes_mios[0].planificado, [])

    def test_sin_asignacion_no_falla_ni_inventa(self):
        self._registro()
        dia = svc.dias_por_aprobar(self.pm)[0]
        self.assertEqual(dia.pendientes_mios[0].planificado, [])

    def test_una_asignacion_sin_actividad_no_aporta_nada(self):
        self._asignacion(self.proyecto, "")
        self._registro()

        dia = svc.dias_por_aprobar(self.pm)[0]
        self.assertEqual(dia.pendientes_mios[0].planificado, [])

    def test_no_hace_una_consulta_por_renglon(self):
        """Con cien renglones en la cola, una consulta por fila es la diferencia
        entre una pantalla y un tiempo de espera."""
        for i in range(6):
            recurso = Recurso.objects.create(
                nombre=f"Persona {i}", email=f"p{i}@test.com", banda="JR",
            )
            dia = DiaLegalizado.objects.create(
                recurso=recurso, fecha=self.fecha, estado=DiaLegalizado.REGISTRADO,
                total_horas=Decimal("8.5"), jornada_esperada=Decimal("8.5"),
            )
            RegistroHoras.objects.create(
                dia=dia, tipo_actividad=self.tipo, proyecto=self.proyecto,
                horas=Decimal("8.5"), detalle="Trabajo",
            )
            Asignacion.objects.create(
                recurso=recurso, proyecto=self.proyecto, actividad=f"Tarea {i}",
                modo_asignacion="RANGO", fecha_inicio=self.fecha, fecha_fin=self.fecha,
                dias_habiles=1, horas_totales=9, intensidad_diaria=Decimal("8.5"),
                estado="APROBADA", solicitada_por=self.pm,
            )

        with CaptureQueriesContext(connection) as consultas:
            dias = svc.dias_por_aprobar(self.pm)

        self.assertEqual(len(dias), 6)
        sobre_asignaciones = [
            c["sql"] for c in consultas.captured_queries
            if "assignments_asignacion" in c["sql"]
        ]
        self.assertEqual(
            len(sobre_asignaciones), 1,
            f"se esperaba una sola consulta de asignaciones, hubo {len(sobre_asignaciones)}",
        )
