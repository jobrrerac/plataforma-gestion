"""El borrado masivo del admin tiene que respetar el soft-delete.

`SoftDeleteModel.delete()` es un metodo de instancia. La accion "Eliminar
seleccionados" del admin no lo usa: llama a `queryset.delete()`, que ejecuta un
DELETE real. Antes del mixin, borrar desde la ficha marcaba el objeto como
eliminado y borrarlo desde la lista lo hacia desaparecer de verdad: el mismo
boton, dos comportamientos.

Solo se salvaban los objetos protegidos por una FK PROTECT, y eso es un
accidente afortunado, no una salvaguarda.
"""

from datetime import date

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from apps.assignments.admin import AsignacionAdmin
from apps.assignments.models import Asignacion
from apps.calendar_engine.admin import IndisponibilidadAdmin
from apps.calendar_engine.models import Indisponibilidad
from apps.core.admin import ProyectoAdmin, RecursoAdmin
from apps.core.models import Proyecto, Recurso


class BorradoMasivoAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pm = User.objects.create_user(username="pm1", password="Clave2026!")

    def setUp(self):
        self.sitio = AdminSite()
        self.peticion = RequestFactory().post("/admin/")
        self.peticion.user = self.pm

    def _borrar_en_lote(self, admin_cls, modelo, queryset):
        """Reproduce lo que hace la accion 'Eliminar seleccionados'."""
        admin_cls(modelo, self.sitio).delete_queryset(self.peticion, queryset)

    def test_proyecto_sobrevive_al_borrado_masivo(self):
        p = Proyecto.objects.create(
            codigo="QA-BORRAR", nombre="N", cliente="C",
            fecha_inicio=date(2026, 9, 1), pm=self.pm,
        )
        self._borrar_en_lote(ProyectoAdmin, Proyecto, Proyecto.objects.filter(pk=p.pk))

        self.assertFalse(Proyecto.objects.filter(pk=p.pk).exists(), "deberia salir de las consultas normales")
        self.assertTrue(Proyecto.all_objects.filter(pk=p.pk).exists(), "la fila NO deberia haberse borrado")
        self.assertIsNotNone(Proyecto.all_objects.get(pk=p.pk).deleted_at)

    def test_recurso_sobrevive_al_borrado_masivo(self):
        r = Recurso.objects.create(nombre="R", email="r@inetum.com", banda="JR")
        self._borrar_en_lote(RecursoAdmin, Recurso, Recurso.objects.filter(pk=r.pk))

        self.assertFalse(Recurso.objects.filter(pk=r.pk).exists())
        self.assertTrue(Recurso.all_objects.filter(pk=r.pk).exists())

    def test_asignacion_sobrevive_al_borrado_masivo(self):
        # Es la mas sensible: LogAuditoria apunta a ella con PROTECT, y perder
        # la fila romperia la trazabilidad append-only.
        recurso = Recurso.objects.create(nombre="R2", email="r2@inetum.com", banda="SR")
        proyecto = Proyecto.objects.create(
            codigo="QA-ASIG", nombre="N", cliente="C",
            fecha_inicio=date(2026, 9, 1), pm=self.pm,
        )
        a = Asignacion.objects.create(
            recurso=recurso, proyecto=proyecto,
            fecha_inicio=date(2026, 9, 1), fecha_fin=date(2026, 9, 4),
            horas_totales=34, intensidad_diaria=8.5,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        self._borrar_en_lote(AsignacionAdmin, Asignacion, Asignacion.objects.filter(pk=a.pk))

        self.assertFalse(Asignacion.objects.filter(pk=a.pk).exists())
        self.assertTrue(Asignacion.all_objects.filter(pk=a.pk).exists())

    def test_indisponibilidad_sobrevive_al_borrado_masivo(self):
        recurso = Recurso.objects.create(nombre="R3", email="r3@inetum.com", banda="JR")
        i = Indisponibilidad.objects.create(
            recurso=recurso, fecha_inicio=date(2026, 9, 1),
            fecha_fin=date(2026, 9, 2), tipo="VACACION",
        )
        self._borrar_en_lote(IndisponibilidadAdmin, Indisponibilidad, Indisponibilidad.objects.filter(pk=i.pk))

        self.assertFalse(Indisponibilidad.objects.filter(pk=i.pk).exists())
        self.assertTrue(Indisponibilidad.all_objects.filter(pk=i.pk).exists())

    def test_varios_a_la_vez(self):
        for n in range(3):
            Proyecto.objects.create(
                codigo=f"QA-LOTE-{n}", nombre="N", cliente="C",
                fecha_inicio=date(2026, 9, 1), pm=self.pm,
            )
        qs = Proyecto.objects.filter(codigo__startswith="QA-LOTE-")
        self._borrar_en_lote(ProyectoAdmin, Proyecto, qs)

        self.assertEqual(Proyecto.objects.filter(codigo__startswith="QA-LOTE-").count(), 0)
        self.assertEqual(Proyecto.all_objects.filter(codigo__startswith="QA-LOTE-").count(), 3)
