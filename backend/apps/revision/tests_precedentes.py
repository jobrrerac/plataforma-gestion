"""Precedentes: qué declaró antes esta persona para algo parecido.

Es la mitad léxica de la búsqueda híbrida del diseño. Lo que se cuida:

- que el **alcance** se aplique antes que la similitud. El parecido más alto del
  histórico entero es ruido; dentro de la misma persona o del mismo proyecto es
  precedente. Sin este filtro la pantalla enseña textos de gente que no tiene
  nada que ver, y quien aprueba deja de mirarla;
- que lo **devuelto** salga primero, porque `motivo_devolucion` es la única
  etiqueta real de qué se rechaza aquí;
- que el número de consultas dependa de cuántos renglones estén marcados, **no**
  del tamaño de la cola. Una consulta por fila en una cola de cien es
  exactamente el problema que este módulo viene a evitar.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.legalizacion import services as svc
from apps.legalizacion.models import DiaLegalizado, RegistroHoras, TipoActividad
from apps.revision import precedentes as prec
from apps.revision.api import clasificar

HOY = date(2026, 9, 14)
TEXTO = "Ajustes al conector de Oracle y pruebas del pipeline"


class BasePrecedentes(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm_prec", "pm@test.com", "clave-larga-123")
        self.proyecto = Proyecto.objects.create(
            codigo="V-25188808/Q", nombre="Simulador", cliente="ANECOOP",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        self.interno = Proyecto.objects.create(
            codigo="INT-DEPART", nombre="Departamentales", cliente="Inetum",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm, facturable=False,
        )
        self.recurso = Recurso.objects.create(
            nombre="Guzman-Mejia Daniel-Fernando", email="daniel@test.com", banda="SR",
        )
        self.otro = Recurso.objects.create(
            nombre="Medina-Novoa Martin", email="martin@test.com", banda="SSR",
        )
        self.tipo = TipoActividad.objects.create(nombre="Proyecto", requiere_proyecto=True)

    def _dia(self, recurso=None, fecha=HOY, estado=DiaLegalizado.REGISTRADO):
        return DiaLegalizado.objects.create(
            recurso=recurso or self.recurso, fecha=fecha, estado=estado,
            total_horas=Decimal("8.5"), jornada_esperada=Decimal("8.5"),
        )

    def _renglon(self, dia, detalle, horas="8.5", proyecto=None,
                 estado=RegistroHoras.PENDIENTE, motivo=""):
        return RegistroHoras.objects.create(
            dia=dia, tipo_actividad=self.tipo, proyecto=proyecto or self.proyecto,
            horas=Decimal(horas), detalle=detalle, estado=estado,
            motivo_devolucion=motivo,
        )


class BuscarPrecedentesTests(BasePrecedentes):
    def test_encuentra_lo_parecido_de_la_misma_persona(self):
        anterior = self._dia(fecha=HOY - timedelta(days=7))
        self._renglon(anterior, TEXTO, horas="4.0")

        hoy = self._dia()
        actual = self._renglon(hoy, "Ajustes al conector de Oracle y sus pruebas")

        encontrados = prec.buscar(actual, hoy)
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0].horas, 4.0)
        self.assertIn("El 07/09/2026 declaró 4 h", encontrados[0].frase)

    def test_no_se_encuentra_a_si_mismo(self):
        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)
        self.assertEqual(prec.buscar(actual, hoy), [])

    def test_ignora_lo_que_no_se_parece(self):
        anterior = self._dia(fecha=HOY - timedelta(days=7))
        self._renglon(anterior, "Reunion de seguimiento con el cliente")

        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)
        self.assertEqual(prec.buscar(actual, hoy), [])

    # ── el alcance va antes que la similitud ────────────────────────────────

    def test_no_trae_lo_de_otra_persona_en_otro_proyecto(self):
        """El parecido mas alto del historico entero es ruido."""
        ajeno = Proyecto.objects.create(
            codigo="V-99999999/Z", nombre="Ajeno", cliente="Otro",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        anterior = self._dia(recurso=self.otro, fecha=HOY - timedelta(days=7))
        self._renglon(anterior, TEXTO, proyecto=ajeno)

        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)
        self.assertEqual(prec.buscar(actual, hoy), [])

    def test_si_trae_lo_de_otra_persona_en_el_mismo_proyecto(self):
        """Un compañero en el mismo proyecto si es precedente."""
        anterior = self._dia(recurso=self.otro, fecha=HOY - timedelta(days=7))
        self._renglon(anterior, TEXTO, horas="2.5")

        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)

        encontrados = prec.buscar(actual, hoy)
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0].persona, "Medina-Novoa Martin")

    def test_no_mira_mas_de_un_ano_atras(self):
        viejo = self._dia(fecha=HOY - timedelta(days=400))
        self._renglon(viejo, TEXTO)

        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)
        self.assertEqual(prec.buscar(actual, hoy), [])

    def test_no_mira_hacia_delante(self):
        futuro = self._dia(fecha=HOY + timedelta(days=7))
        self._renglon(futuro, TEXTO)

        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)
        self.assertEqual(prec.buscar(actual, hoy), [])

    def test_un_dia_abierto_no_es_precedente(self):
        """Todavia se esta escribiendo: nadie lo ha mirado."""
        abierto = self._dia(fecha=HOY - timedelta(days=7), estado=DiaLegalizado.ABIERTO)
        self._renglon(abierto, TEXTO)

        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)
        self.assertEqual(prec.buscar(actual, hoy), [])

    # ── lo devuelto pesa mas ────────────────────────────────────────────────

    def test_lo_devuelto_va_primero_aunque_se_parezca_menos(self):
        aprobado = self._dia(fecha=HOY - timedelta(days=3))
        self._renglon(aprobado, TEXTO, estado=RegistroHoras.APROBADO)

        devuelto = self._dia(fecha=HOY - timedelta(days=10))
        self._renglon(
            devuelto, "Ajustes al conector de Oracle", estado=RegistroHoras.DEVUELTO,
            motivo="Falta decir que se ajusto",
        )

        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)

        encontrados = prec.buscar(actual, hoy)
        self.assertTrue(encontrados[0].devuelto, "el devuelto no salio primero")
        self.assertEqual(encontrados[0].motivo, "Falta decir que se ajusto")

    def test_como_mucho_tres(self):
        for i in range(6):
            anterior = self._dia(fecha=HOY - timedelta(days=i + 1))
            self._renglon(anterior, TEXTO)

        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)
        self.assertEqual(len(prec.buscar(actual, hoy)), 3)

    def test_la_frase_dice_que_es_historial_y_no_una_accion(self):
        """Con el formato de etiqueta, «devuelto» quedaba encima de los botones
        Aprobar y Devolver y se leia como el estado del renglon actual."""
        devuelto = self._dia(fecha=HOY - timedelta(days=5))
        self._renglon(
            devuelto, TEXTO, horas="4.0", estado=RegistroHoras.DEVUELTO,
            motivo="Di que pruebas",
        )
        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)

        frase = prec.buscar(actual, hoy)[0].frase
        self.assertIn("El 09/09/2026 declaró 4 h", frase)
        self.assertIn("se lo devolvieron", frase)
        self.assertIn("Di que pruebas", frase)

    def test_la_frase_nombra_a_quien_no_es_uno_mismo(self):
        anterior = self._dia(recurso=self.otro, fecha=HOY - timedelta(days=5))
        self._renglon(anterior, TEXTO, horas="2.0", estado=RegistroHoras.APROBADO)

        hoy = self._dia()
        actual = self._renglon(hoy, TEXTO)

        frase = prec.buscar(actual, hoy)[0].frase
        self.assertIn("Medina-Novoa Martin declaró", frase)
        self.assertIn("se aprobó", frase)

    def test_sin_detalle_no_busca(self):
        hoy = self._dia()
        actual = self._renglon(hoy, "")
        self.assertEqual(prec.buscar(actual, hoy), [])


class AdjuntarALaColaTests(BasePrecedentes):
    def _cola(self):
        dias = svc.dias_por_aprobar(self.pm)
        clasificar(dias, self.pm)
        return dias

    def _plan(self, recurso, fecha=HOY):
        """Asignacion aprobada, para que el renglon no salte por SIN_PLAN."""
        return Asignacion.objects.create(
            recurso=recurso, proyecto=self.proyecto, modo_asignacion="RANGO",
            fecha_inicio=fecha, fecha_fin=fecha, dias_habiles=1, horas_totales=9,
            intensidad_diaria=Decimal("8.5"), estado="APROBADA", solicitada_por=self.pm,
        )

    def _consultas_de_precedentes(self, dias):
        with CaptureQueriesContext(connection) as consultas:
            clasificar(dias, self.pm)
        return [c for c in consultas.captured_queries if "similarity" in c["sql"].lower()]

    def test_solo_los_marcados_reciben_precedentes(self):
        """Los de rutina no los necesitan: no hay nada que decidir en ellos."""
        anterior = self._dia(fecha=HOY - timedelta(days=7))
        self._renglon(anterior, "muchas tareas", proyecto=self.interno)

        self._plan(self.recurso)
        hoy = self._dia()
        self._renglon(hoy, TEXTO, horas="4.0")
        self._renglon(hoy, "muchas tareas", horas="4.5", proyecto=self.interno)

        dia = self._cola()[0]
        por_detalle = {r.detalle: r for r in dia.pendientes_mios}
        self.assertFalse(hasattr(por_detalle[TEXTO], "precedentes"))
        self.assertTrue(por_detalle["muchas tareas"].precedentes)

    def test_un_renglon_de_rutina_no_dispara_ninguna_consulta(self):
        """Una consulta por fila en una cola de cien es el problema que este
        modulo viene a evitar."""
        for i in range(10):
            recurso = Recurso.objects.create(
                nombre=f"Persona {i}", email=f"p{i}@test.com", banda="JR",
            )
            self._plan(recurso)
            dia = self._dia(recurso=recurso)
            self._renglon(dia, f"Desarrollo del modulo numero {i} y sus pruebas")

        dias = svc.dias_por_aprobar(self.pm)
        self.assertEqual(
            self._consultas_de_precedentes(dias), [],
            "se buscaron precedentes de renglones que no estaban marcados",
        )

    def test_con_muchos_marcados_el_tope_los_contiene(self):
        """Sin tope, una cola llena de renglones marcados serian tantas
        consultas como filas. El tope es lo que lo impide."""
        for i in range(25):
            recurso = Recurso.objects.create(
                nombre=f"Marcada {i}", email=f"m{i}@test.com", banda="JR",
            )
            dia = self._dia(recurso=recurso)
            self._renglon(dia, "muchas tareas", proyecto=self.interno)

        dias = svc.dias_por_aprobar(self.pm)
        consultas = self._consultas_de_precedentes(dias)
        self.assertEqual(
            len(consultas), prec.MAX_RENGLONES,
            f"25 renglones marcados dispararon {len(consultas)} consultas",
        )
