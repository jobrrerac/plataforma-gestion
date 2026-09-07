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
from decimal import Decimal

from apps.core.models import Recurso
from apps.legalizacion import services as svc
from apps.legalizacion.models import DiaLegalizado, RegistroHoras, TipoActividad


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
            TipoActividad.objects.filter(requiere_proyecto=False, activo=True)
            .values_list("nombre", flat=True)
        )
        self.assertEqual(sin_proyecto, {"Estudio", "Entrenamiento"})

    def test_formacion_queda_retirada_pero_no_borrada(self):
        # Se solapaba tanto con Entrenamiento que se habria elegido al azar.
        # Se desactiva en vez de borrarse: si algun dia hubiera horas imputadas
        # a ella, borrarla se las llevaria por delante.
        formacion = TipoActividad.objects.filter(nombre="Formacion").first()
        if formacion is not None:
            self.assertFalse(formacion.activo)
        self.assertNotIn(
            "Formacion",
            TipoActividad.objects.filter(activo=True).values_list("nombre", flat=True),
        )

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
        self.assertEqual(TipoActividad.objects.filter(activo=True).count(), 3)

    def test_no_desactiva_las_que_anada_un_admin(self):
        # El comando corre en cada despliegue: si retirara todo lo que no
        # conoce, borraria del desplegable las actividades creadas a mano.
        propia = TipoActividad.objects.create(nombre="Preventa", orden=90)
        call_command("setup_actividades", verbosity=0)
        propia.refresh_from_db()
        self.assertTrue(propia.activo)

    def test_se_ordenan_para_el_desplegable(self):
        primero = TipoActividad.objects.first()
        self.assertEqual(primero.nombre, "Proyecto")

    def test_entrenamiento_y_estudio_se_distinguen_por_quien_ensena(self):
        # Es la unica linea que las separa, y tiene que estar en el texto o la
        # gente elegira a ojo.
        entrenamiento = TipoActividad.objects.get(nombre="Entrenamiento")
        estudio = TipoActividad.objects.get(nombre="Estudio")
        self.assertIn("Alguien te formo", entrenamiento.descripcion)
        self.assertIn("por tu cuenta", estudio.descripcion)

    def test_todas_explican_cuando_usarlas(self):
        # Tres categorías parecidas sin una frase que las separe se rellenan al
        # azar, y entonces el informe de en qué se va el tiempo no dice nada.
        for actividad in TipoActividad.objects.filter(activo=True):
            self.assertTrue(
                actividad.descripcion.strip(),
                f"«{actividad.nombre}» no explica cuándo usarla",
            )

    def test_cada_descripcion_es_distinta(self):
        # Si dos se pudieran describir igual, sobraría una.
        descripciones = list(
            TipoActividad.objects.filter(activo=True).values_list("descripcion", flat=True)
        )
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


class BorrarUnConjuntoTambienEsSoftDeleteTests(TestCase):
    """`queryset.delete()` no puede borrar filas de verdad.

    Lo reportó QA: registró unas horas, las borró, las volvió a registrar y
    dejaron de sumar. Al mirar producción faltaban **194 filas** de
    `RegistroHoras` — no marcadas como borradas: ausentes de la tabla.

    La causa: `SoftDeleteModel.delete()` es un método de **instancia**, y
    `legalizacion/services.py` reemplazaba los renglones no aprobados con
    `dia.registros.exclude(...).delete()`, que es otro método y baja directo a
    SQL. Al lado había un comentario que decía «soft-delete».

    Es la misma trampa que ya obligó a escribir `SoftDeleteAdminMixin` para el
    borrado masivo del admin. Allí se tapó en el admin; el agujero seguía
    abierto para cualquier código de servicio.

    Va contra la primera regla no negociable del proyecto: soft-delete en todas
    las entidades, nunca borrado físico. Y el daño no es solo perder datos:
    corregir un día borraba la evidencia de qué había antes, que es justo lo que
    hace falta para investigar un caso como el que lo destapó.
    """

    def setUp(self):
        self.recurso = Recurso.objects.create(
            nombre="Borra Conjuntos", email="bc@test.com", banda="SR",
        )
        self.tipo = TipoActividad.objects.create(nombre="Estudio SD", requiere_proyecto=False)
        self.dia = DiaLegalizado.objects.create(
            # ABIERTO: `guardar_renglones` solo toca dias editables, y el caso
            # que se prueba es corregir un dia antes de cerrarlo.
            recurso=self.recurso, fecha=date(2026, 1, 5), estado=DiaLegalizado.ABIERTO,
            total_horas=Decimal("4"), jornada_esperada=Decimal("8.5"),
        )
        for texto in ("uno", "dos"):
            RegistroHoras.objects.create(
                dia=self.dia, tipo_actividad=self.tipo, horas=Decimal("2"), detalle=texto,
            )

    def _filas(self):
        return RegistroHoras.all_objects.filter(dia=self.dia).count()

    def _visibles(self):
        return RegistroHoras.objects.filter(dia=self.dia).count()

    def test_borrar_un_conjunto_conserva_las_filas(self):
        self.dia.registros.all().delete()
        self.assertEqual(self._visibles(), 0, "deberian dejar de verse")
        self.assertEqual(self._filas(), 2, "pero las filas tienen que seguir ahi")

    def test_las_filas_quedan_marcadas_con_la_fecha(self):
        self.dia.registros.all().delete()
        for r in RegistroHoras.all_objects.filter(dia=self.dia):
            self.assertIsNotNone(r.deleted_at)

    def test_borrar_una_instancia_sigue_funcionando_igual(self):
        RegistroHoras.objects.filter(dia=self.dia).first().delete()
        self.assertEqual(self._visibles(), 1)
        self.assertEqual(self._filas(), 2)

    def test_volver_a_guardar_el_dia_no_borra_lo_anterior(self):
        """El caso real que lo destapo: corregir un dia ya registrado.

        `guardar_renglones` reemplaza los renglones no aprobados. Antes los
        hacia desaparecer de la tabla, asi que no quedaba forma de saber que
        habia declarado la persona en el primer intento.
        """
        svc.guardar_renglones(self.dia, [
            {"tipo_actividad": self.tipo, "proyecto": None,
             "horas": "3", "detalle": "lo que quedo tras corregir"},
        ])
        self.assertEqual(self._visibles(), 1, "deberia quedar solo el renglon nuevo")
        self.assertGreaterEqual(
            self._filas(), 3,
            "los dos renglones anteriores se perdieron al corregir el dia",
        )

    def test_hard_delete_sigue_existiendo_para_quien_lo_necesite(self):
        """Limpiar datos de prueba tiene que poder hacerse, pero a proposito."""
        RegistroHoras.objects.filter(dia=self.dia).hard_delete()
        self.assertEqual(self._filas(), 0)

    def test_all_objects_borra_de_verdad(self):
        """Es la via de los scripts de limpieza y no lleva red, a proposito."""
        RegistroHoras.all_objects.filter(dia=self.dia).delete()
        self.assertEqual(self._filas(), 0)
