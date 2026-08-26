"""Tests de los cimientos de la legalización de horas.

Fase 1: catálogo de actividades, el flag de facturable en `Proyecto`, y que la
jornada del día siga siendo la correcta tras documentarla.
"""

from datetime import date

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase

from apps.assignments.services import (
    JORNADA_LUNES_JUEVES,
    JORNADA_VIERNES,
    JORNADA_VIGENTE_DESDE,
    capacidad_maxima_dia,
)
from apps.core.models import Proyecto
from apps.legalizacion.models import TipoActividad


class CatalogoActividadesTests(TestCase):
    def setUp(self):
        call_command("setup_actividades", verbosity=0)

    def test_solo_proyecto_exige_indicar_cual(self):
        con_proyecto = set(
            TipoActividad.objects.filter(requiere_proyecto=True).values_list("nombre", flat=True)
        )
        self.assertEqual(con_proyecto, {"Proyecto"})

    def test_las_demas_no_piden_nada_mas(self):
        sin_proyecto = set(
            TipoActividad.objects.filter(requiere_proyecto=False).values_list("nombre", flat=True)
        )
        self.assertEqual(sin_proyecto, {"Formacion", "Estudio", "Entrenamiento"})

    def test_departamentales_y_management_no_son_actividades(self):
        # Son proyectos internos: van como Proyecto para que la clave foránea
        # garantice que siempre signifiquen lo mismo. Si alguien los añade aquí,
        # vuelve el texto libre que este módulo viene a eliminar.
        nombres = {n.lower() for n in TipoActividad.objects.values_list("nombre", flat=True)}
        self.assertNotIn("departamentales", nombres)
        self.assertNotIn("managment", nombres)
        self.assertNotIn("management", nombres)

    def test_el_comando_es_idempotente(self):
        call_command("setup_actividades", verbosity=0)
        call_command("setup_actividades", verbosity=0)
        self.assertEqual(TipoActividad.objects.count(), 4)

    def test_se_ordenan_para_el_desplegable(self):
        primero = TipoActividad.objects.first()
        self.assertEqual(primero.nombre, "Proyecto")

    def test_todas_explican_cuando_usarlas(self):
        # Tres categorías parecidas sin una frase que las separe se rellenan al
        # azar, y entonces el informe de en qué se va el tiempo no dice nada.
        for actividad in TipoActividad.objects.all():
            self.assertTrue(
                actividad.descripcion.strip(),
                f"«{actividad.nombre}» no explica cuándo usarla",
            )

    def test_cada_descripcion_es_distinta(self):
        # Si dos se pudieran describir igual, sobraría una.
        descripciones = list(TipoActividad.objects.values_list("descripcion", flat=True))
        self.assertEqual(len(descripciones), len(set(descripciones)))

    def test_desactivar_no_borra_el_historico(self):
        act = TipoActividad.objects.get(nombre="Estudio")
        act.activo = False
        act.save(update_fields=["activo"])
        self.assertTrue(TipoActividad.objects.filter(nombre="Estudio").exists())


class ProyectoFacturableTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user(username="pm1", password="Clave2026!")

    def _proyecto(self, codigo, **extra):
        return Proyecto.objects.create(
            codigo=codigo, nombre="P", cliente="C",
            fecha_inicio=date(2026, 9, 1), pm=self.pm, **extra
        )

    def test_por_defecto_un_proyecto_es_facturable(self):
        # El backfill de la migración deja así los que ya existían, que son
        # todos de cliente.
        self.assertTrue(self._proyecto("P-001").facturable)

    def test_un_proyecto_interno_se_marca_no_facturable(self):
        interno = self._proyecto("P-INT", facturable=False)
        self.assertFalse(interno.facturable)

    def test_un_proyecto_interno_sigue_siendo_un_proyecto_normal(self):
        # Nada cambia salvo el flag: mismo modelo, mismas reglas, mismo
        # soft-delete. No hay un catálogo paralelo de "cosas internas".
        interno = self._proyecto("P-INT2", facturable=False)
        self.assertEqual(interno.estado, "ACTIVO")
        self.assertIn(interno, Proyecto.objects.all())

    def test_se_pueden_separar_facturables_de_internos(self):
        self._proyecto("P-CLI")
        self._proyecto("P-INT3", facturable=False)
        self.assertEqual(Proyecto.objects.filter(facturable=True).count(), 1)
        self.assertEqual(Proyecto.objects.filter(facturable=False).count(), 1)


class JornadaTests(TestCase):
    """La jornada colombiana vigente: 42 h semanales desde el 15/07/2026."""

    def test_lunes_a_jueves_son_ocho_y_media(self):
        for dia in range(14, 18):  # lun 14 a jue 17 de septiembre de 2026
            fecha = date(2026, 9, dia)
            self.assertEqual(capacidad_maxima_dia(fecha), 8.5, f"fallo el {fecha}")

    def test_el_viernes_son_ocho(self):
        self.assertEqual(capacidad_maxima_dia(date(2026, 9, 18)), 8.0)

    def test_la_semana_suma_cuarenta_y_dos(self):
        # Es la comprobación que de verdad importa: 8.5×4 + 8 = 42, la jornada
        # legal colombiana desde julio de 2026.
        semana = sum(capacidad_maxima_dia(date(2026, 9, d)) for d in range(14, 19))
        self.assertEqual(semana, 42.0)

    def test_la_fecha_de_vigencia_queda_registrada(self):
        # No se usa para calcular todavía, pero documenta desde cuándo vale este
        # número y por qué los datos anteriores muestran otro.
        self.assertEqual(JORNADA_VIGENTE_DESDE, date(2026, 7, 15))

    def test_las_constantes_son_las_esperadas(self):
        self.assertEqual(JORNADA_LUNES_JUEVES, 8.5)
        self.assertEqual(JORNADA_VIERNES, 8.0)


class PermisosCatalogoTests(TestCase):
    def setUp(self):
        call_command("setup_grupos", verbosity=0)

    def test_el_ingeniero_puede_consultar_el_catalogo(self):
        grupo = Group.objects.get(name="Ingeniero")
        codigos = set(grupo.permissions.values_list("codename", flat=True))
        self.assertIn("view_tipoactividad", codigos)

    def test_el_ingeniero_no_puede_editarlo(self):
        grupo = Group.objects.get(name="Ingeniero")
        codigos = set(grupo.permissions.values_list("codename", flat=True))
        self.assertNotIn("add_tipoactividad", codigos)
        self.assertNotIn("change_tipoactividad", codigos)

    def test_el_admin_lo_administra(self):
        grupo = Group.objects.get(name="Admin")
        codigos = set(grupo.permissions.values_list("codename", flat=True))
        for accion in ("add", "change", "delete", "view"):
            self.assertIn(f"{accion}_tipoactividad", codigos)
