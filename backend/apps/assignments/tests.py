from django.test import TestCase
from django.contrib.auth.models import User
from datetime import date
from apps.core.models import Recurso, Proyecto
from .models import Asignacion, LogAuditoria
from .services import (
    calcular_fecha_fin, puede_asignar, aprobar_asignacion,
    analizar_recurrencia, crear_solicitudes_recurrentes,
)


class CalculoFechaFinTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm", password="pass")
        self.recurso = Recurso.objects.create(nombre="Dev1", email="dev1@test.com", banda="SR")

    def test_40h_a_8h_dia(self):
        # 40h / 8h = 5 días, lunes 13 ene → viernes 17 ene 2025
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 40, 8)
        self.assertEqual(fecha, date(2025, 1, 17))

    def test_20h_a_4h_dia(self):
        # 20h / 4h = 5 días, lunes 13 ene → viernes 17 ene 2025
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 20, 4)
        self.assertEqual(fecha, date(2025, 1, 17))

    def test_8h_a_8h_dia(self):
        # 1 día, lunes → mismo lunes
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 8, 8)
        self.assertEqual(fecha, date(2025, 1, 13))

    def test_cruza_fin_de_semana(self):
        # 16h / 8h = 2 días, viernes 10 ene → lunes 13 ene
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 10), 16, 8)
        self.assertEqual(fecha, date(2025, 1, 13))


class CapacidadTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm2", password="pass")
        self.admin = User.objects.create_user("admin2", password="pass")
        self.recurso = Recurso.objects.create(nombre="Dev2", email="dev2@test.com", banda="SR")
        self.proyecto = Proyecto.objects.create(
            codigo="P-001", nombre="Alpha", cliente="X",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )

    def _crear_aprobada(self, horas=40, intensidad=8, inicio=date(2025, 1, 13)):
        fecha_fin = calcular_fecha_fin(self.recurso, inicio, horas, intensidad)
        a = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=horas, intensidad_diaria=intensidad,
            fecha_inicio=inicio, fecha_fin=fecha_fin,
            estado="APROBADA", solicitada_por=self.pm,
        )
        return a

    def test_sobreasignacion_bloqueada_lunes(self):
        # Lunes: tope 8.5 h. Primera asignación ocupa 8.5 h → segunda de 1 h debe bloquearse
        self._crear_aprobada(horas=43, intensidad=8.5)  # 43/8.5 = 5 días lun-vie
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 8, 1)
        candidata = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=8, intensidad_diaria=1,
            fecha_inicio=date(2025, 1, 13), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(candidata)
        self.assertFalse(ok)

    def test_combinacion_4h_mas_4h_valido_lunes(self):
        # Lunes: tope 8.5 h. 4 + 4 = 8 h → cabe
        self._crear_aprobada(horas=20, intensidad=4)
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 20, 4)
        candidata = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=20, intensidad_diaria=4,
            fecha_inicio=date(2025, 1, 13), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(candidata)
        self.assertTrue(ok)

    def test_sobreasignacion_viernes_tope_8h(self):
        # Viernes 17 ene 2025: tope 8 h. 8 + 1 debe bloquearse
        self._crear_aprobada(horas=8, intensidad=8, inicio=date(2025, 1, 17))
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 17), 8, 1)
        candidata = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=8, intensidad_diaria=1,
            fecha_inicio=date(2025, 1, 17), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(candidata)
        self.assertFalse(ok)

    def test_log_auditoria_se_crea_al_aprobar(self):
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 40, 8)
        asig = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=40, intensidad_diaria=8,
            fecha_inicio=date(2025, 1, 13), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        aprobar_asignacion(asig, self.admin)
        self.assertEqual(asig.estado, "APROBADA")
        self.assertTrue(LogAuditoria.objects.filter(asignacion=asig, accion="APROBAR").exists())


class RecurrenciaTests(TestCase):
    """Solicitudes recurrentes: patrón semanal → serie de asignaciones de un día."""

    def setUp(self):
        self.pm = User.objects.create_user("pm_rec", password="pass")
        self.recurso = Recurso.objects.create(nombre="DevRec", email="devrec@test.com", banda="SR")
        self.proyecto = Proyecto.objects.create(
            codigo="P-REC", nombre="Rec", cliente="X",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )

    def test_proximos_4_lunes_4h(self):
        serie, creadas, omitidas = crear_solicitudes_recurrentes(
            self.recurso, self.proyecto, date(2025, 1, 13), 4, {0: 4.0}, self.pm,
        )
        self.assertEqual(
            [a.fecha_inicio for a in creadas],
            [date(2025, 1, 13), date(2025, 1, 20), date(2025, 1, 27), date(2025, 2, 3)],
        )
        self.assertEqual(omitidas, [])
        self.assertTrue(all(a.serie == serie for a in creadas))
        self.assertTrue(all(a.estado == "SOLICITADA" for a in creadas))
        self.assertTrue(all(a.fecha_inicio == a.fecha_fin for a in creadas))
        self.assertEqual(
            LogAuditoria.objects.filter(asignacion__serie=serie, accion="CREAR").count(), 4,
        )

    def test_patron_lunes_miercoles_viernes(self):
        # "esta semana: lunes 2h, miércoles 4h y viernes 2h"
        _, creadas, _ = crear_solicitudes_recurrentes(
            self.recurso, self.proyecto, date(2025, 1, 13), 1,
            {0: 2.0, 2: 4.0, 4: 2.0}, self.pm,
        )
        self.assertEqual(
            [(a.fecha_inicio, float(a.intensidad_diaria)) for a in creadas],
            [(date(2025, 1, 13), 2.0), (date(2025, 1, 15), 4.0), (date(2025, 1, 17), 2.0)],
        )

    def test_omite_feriado(self):
        # Lunes 6 ene 2025 = Reyes Magos (festivo). Patrón de 2 lunes desde el 6.
        _, creadas, omitidas = crear_solicitudes_recurrentes(
            self.recurso, self.proyecto, date(2025, 1, 6), 2, {0: 4.0}, self.pm,
        )
        self.assertEqual([a.fecha_inicio for a in creadas], [date(2025, 1, 13)])
        self.assertEqual(len(omitidas), 1)
        self.assertEqual(omitidas[0]["estado"], "NO_HABIL")
        self.assertEqual(omitidas[0]["fecha"], date(2025, 1, 6))

    def test_omite_dia_sin_cupo(self):
        # El lunes 13 ya está a jornada completa → solo se crea el lunes 20
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2025, 1, 13), fecha_fin=date(2025, 1, 13),
            horas_totales=9, intensidad_diaria=8.0, jornada_completa=True,
            estado="APROBADA", solicitada_por=self.pm,
        )
        _, creadas, omitidas = crear_solicitudes_recurrentes(
            self.recurso, self.proyecto, date(2025, 1, 13), 2, {0: 4.0}, self.pm,
        )
        self.assertEqual([a.fecha_inicio for a in creadas], [date(2025, 1, 20)])
        self.assertEqual(omitidas[0]["estado"], "SIN_CUPO")

    def test_error_si_ningun_dia_viable(self):
        with self.assertRaises(ValueError):
            crear_solicitudes_recurrentes(
                self.recurso, self.proyecto, date(2025, 1, 6), 1, {0: 4.0}, self.pm,
            )

    def test_analizar_no_escribe(self):
        plan = analizar_recurrencia(self.recurso, date(2025, 1, 13), 2, {0: 4.0})
        self.assertEqual(len(plan), 2)
        self.assertEqual(Asignacion.objects.count(), 0)

    def test_aprobacion_de_un_dia_de_la_serie_respeta_capacidad(self):
        # Cada día de la serie pasa por el motor de aprobación normal
        _, creadas, _ = crear_solicitudes_recurrentes(
            self.recurso, self.proyecto, date(2025, 1, 13), 1, {0: 8.5}, self.pm,
        )
        aprobar_asignacion(creadas[0], self.pm)
        self.assertEqual(creadas[0].estado, "APROBADA")
        # Una segunda solicitud de 1h ese lunes ya no cabe
        candidata = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=1, intensidad_diaria=1,
            fecha_inicio=date(2025, 1, 13), fecha_fin=date(2025, 1, 13),
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(candidata)
        self.assertFalse(ok)
