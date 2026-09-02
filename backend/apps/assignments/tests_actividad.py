"""La actividad del cronograma como campo de la asignación.

Hasta ahora la tarea ("Integración Oracle") solo vivía dentro de
`LogAuditoria.detalle["actividad"]`, y eso tenía dos problemas: el log es
append-only porque registra eventos, no estado que pueda corregirse, y solo lo
rellenaba la carga masiva —las asignaciones creadas desde la pantalla no
llevaban nada.

Lo que se prueba aquí es el valor que justifica el campo: que quien aprueba
horas pueda ver qué se había planificado al lado de lo que la persona declara.
Y el relleno hacia atrás, que en local no se pudo comprobar contra datos reales
porque las 21 asignaciones cargadas están en producción, no aquí.
"""

from datetime import date
from decimal import Decimal
from importlib import import_module

from django.apps import apps as registro_de_apps
from django.contrib.auth.models import User
from django.test import TestCase

from apps.assignments.models import Asignacion, LogAuditoria
from apps.assignments.services import crear_solicitud
from apps.core.models import Proyecto, Recurso

# El modulo empieza por un numero, asi que no se puede importar con `from ... import`.
rellenar_desde_el_log = import_module(
    "apps.assignments.migrations.0012_asignacion_actividad"
).rellenar_desde_el_log


class ActividadEnLaAsignacionTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm_act", "pm@test.com", "x")
        self.proyecto = Proyecto.objects.create(
            codigo="V-25000001/Q", nombre="Simulador", cliente="ACME",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        self.recurso = Recurso.objects.create(
            nombre="Medina-Novoa Martin", email="martin@test.com", banda="SSR",
        )

    def _asignacion(self, actividad="", estado="APROBADA"):
        return Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto, actividad=actividad,
            modo_asignacion="RANGO",
            fecha_inicio=date(2026, 9, 14), fecha_fin=date(2026, 9, 15),
            dias_habiles=2, horas_totales=9, intensidad_diaria=Decimal("4.3"),
            estado=estado, solicitada_por=self.pm,
        )

    def test_es_opcional(self):
        """Obligarla frenaria el alta de asignaciones que hoy se crean sin ella."""
        asignacion = self._asignacion()
        self.assertEqual(asignacion.actividad, "")

    def test_la_pantalla_de_solicitud_la_guarda(self):
        asignacion = crear_solicitud(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2026, 9, 14), fecha_fin=date(2026, 9, 15),
            intensidad_diaria=4, jornada_completa=False, solicitante=self.pm,
            actividad="  Integración Oracle  ",
        )
        self.assertEqual(asignacion.actividad, "Integración Oracle")

    def test_sin_actividad_la_solicitud_sigue_funcionando(self):
        asignacion = crear_solicitud(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2026, 9, 14), fecha_fin=date(2026, 9, 15),
            intensidad_diaria=4, jornada_completa=False, solicitante=self.pm,
        )
        self.assertEqual(asignacion.actividad, "")

    def test_tambien_queda_en_el_log(self):
        """El campo dice cual es la tarea ahora; el log, con cual se dio de alta."""
        asignacion = crear_solicitud(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2026, 9, 14), fecha_fin=date(2026, 9, 15),
            intensidad_diaria=4, jornada_completa=False, solicitante=self.pm,
            actividad="Integración Oracle",
        )
        log = LogAuditoria.objects.get(asignacion=asignacion, accion="CREAR")
        self.assertEqual(log.detalle["actividad"], "Integración Oracle")

    def test_corregir_la_actividad_no_toca_el_log(self):
        """Es justo lo que no se podia hacer teniendola solo en el log."""
        asignacion = crear_solicitud(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2026, 9, 14), fecha_fin=date(2026, 9, 15),
            intensidad_diaria=4, jornada_completa=False, solicitante=self.pm,
            actividad="Integracion Oracl",
        )
        asignacion.actividad = "Integración Oracle"
        asignacion.save(update_fields=["actividad", "updated_at"])

        asignacion.refresh_from_db()
        self.assertEqual(asignacion.actividad, "Integración Oracle")
        self.assertEqual(
            LogAuditoria.objects.get(asignacion=asignacion, accion="CREAR").detalle["actividad"],
            "Integracion Oracl",
        )


class RellenoDesdeElLogTests(TestCase):
    """El relleno hacia atras de la migracion 0012.

    Se llama a la funcion de la migracion con el registro real de modelos, que
    es lo que `apps.get_model` necesita. En local no habia nada que rellenar
    —las asignaciones con actividad estan en produccion— asi que sin esto el
    relleno se habria aplicado a ciegas sobre los datos reales.
    """

    def setUp(self):
        self.pm = User.objects.create_user("pm_relleno", "pm2@test.com", "x")
        self.proyecto = Proyecto.objects.create(
            codigo="V-25000002/Q", nombre="Simulador", cliente="ACME",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        self.recurso = Recurso.objects.create(
            nombre="Guzman-Mejia Daniel-Fernando", email="daniel@test.com", banda="SR",
        )

    def _asignacion(self):
        return Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto, modo_asignacion="RANGO",
            fecha_inicio=date(2026, 9, 14), fecha_fin=date(2026, 9, 15),
            dias_habiles=2, horas_totales=9, intensidad_diaria=Decimal("4.3"),
            estado="SOLICITADA", solicitada_por=self.pm,
        )

    def test_copia_la_actividad_que_estaba_en_el_log(self):
        asignacion = self._asignacion()
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="CREAR", actor=self.pm,
            detalle={"modo": "RANGO", "actividad": "Integración Oracle"},
        )
        Asignacion.objects.filter(pk=asignacion.pk).update(actividad="")

        rellenar_desde_el_log(registro_de_apps, None)

        asignacion.refresh_from_db()
        self.assertEqual(asignacion.actividad, "Integración Oracle")

    def test_una_creacion_sin_actividad_lo_deja_vacio(self):
        """Las asignaciones creadas desde la pantalla no tenian actividad."""
        asignacion = self._asignacion()
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="CREAR", actor=self.pm,
            detalle={"modo": "RANGO", "dias_habiles": 2},
        )

        rellenar_desde_el_log(registro_de_apps, None)

        asignacion.refresh_from_db()
        self.assertEqual(asignacion.actividad, "")

    def test_con_varias_entradas_gana_la_del_alta(self):
        """El log es append-only: nada impide que haya mas de un CREAR."""
        asignacion = self._asignacion()
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="CREAR", actor=self.pm,
            detalle={"actividad": "La del alta"},
        )
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="CREAR", actor=self.pm,
            detalle={"actividad": "Una posterior"},
        )

        rellenar_desde_el_log(registro_de_apps, None)

        asignacion.refresh_from_db()
        self.assertEqual(asignacion.actividad, "La del alta")
