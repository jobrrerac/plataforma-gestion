"""Observaciones en las dos direcciones, y quién puede leer cada una.

Lo que se blinda aquí son dos cosas que se contradicen solo en apariencia:

- Lo que el proyecto escribe **sobre** una persona, esa persona lo lee. Un
  registro que sirve para evaluar a alguien y que esa persona no puede ver no se
  puede rebatir, y por tanto no se puede considerar justo.
- Lo que una persona escribe **sobre** el proyecto no lo lee el jefe de proyecto.
  Quien depende de otro para que le asignen tareas no va a escribir «llevo tres
  días sin respuesta» sabiendo que esa misma persona lo va a leer con su nombre
  encima.

La asimetría es deliberada y es lo que hace que la segunda dirección produzca
información real en vez de cortesía.
"""

from datetime import date

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.seguimiento import services as svc
from apps.seguimiento.models import Feedback


class BaseFeedback(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm_fb", "af@test.com", "clave-larga-1")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.pm = User.objects.create_user("pm_fb", "pf@test.com", "clave-larga-1")
        self.pm.groups.add(Group.objects.get_or_create(name="PM")[0])
        self.ing = User.objects.create_user("ing_fb", "if@test.com", "clave-larga-1")
        self.ing.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])

        self.recurso = Recurso.objects.create(
            nombre="Observada Gomez", email="if@test.com", banda="JR", usuario=self.ing,
        )
        self.proyecto = Proyecto.objects.create(
            codigo="FB-01", nombre="Proyecto observado", pm=self.pm,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
        )
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
            horas_totales=100, intensidad_diaria=8.5,
            estado="APROBADA", solicitada_por=self.pm,
        )

    def _sobre_la_persona(self, autor=None, **extra):
        datos = dict(
            proyecto=self.proyecto,
            situacion="revision del endpoint del martes",
            conducta="entrego sin probar los casos de borde",
            impacto="hubo que devolverlo y se perdio un dia",
            tipo=Feedback.A_MEJORAR,
        )
        datos.update(extra)
        return svc.registrar_feedback(
            recurso=self.recurso, autor=autor or self.pm,
            direccion=Feedback.PROYECTO_A_RECURSO, **datos
        )

    def _sobre_el_proyecto(self, autor=None, **extra):
        datos = dict(
            proyecto=self.proyecto,
            claridad_objetivo=2,
            tuve_que_intuir=True,
            que_intui="los nombres de las tablas",
            horas_hasta_respuesta=72,
        )
        datos.update(extra)
        return svc.registrar_feedback(
            recurso=self.recurso, autor=autor or self.ing,
            direccion=Feedback.RECURSO_A_PROYECTO, **datos
        )


class ObservacionSobreLaPersonaTests(BaseFeedback):

    def test_el_pm_del_proyecto_puede(self):
        f = self._sobre_la_persona()
        self.assertEqual(f.autor, self.pm)
        self.assertEqual(f.direccion, Feedback.PROYECTO_A_RECURSO)

    def test_sin_conducta_observada_no_se_guarda(self):
        """Sin hechos esto es una opinion. Una observacion con fecha se puede
        contrastar y rebatir; un adjetivo no."""
        with self.assertRaises(ValidationError):
            self._sobre_la_persona(conducta="   ")

    def test_sin_decir_si_es_fortaleza_o_mejora_tampoco(self):
        with self.assertRaises(ValidationError):
            self._sobre_la_persona(tipo="")

    def test_la_dimension_es_opcional(self):
        """La observacion es lo que cuenta; la dimension solo agrupa. Exigirla
        convierte el formulario en un tramite."""
        f = self._sobre_la_persona(dimension="")
        self.assertEqual(f.dimension, "")

    def test_un_pm_ajeno_no_observa_a_quien_no_dirige(self):
        otro_pm = User.objects.create_user("pm2_fb", "p2@test.com", "clave-larga-1")
        otro_pm.groups.add(Group.objects.get(name="PM"))
        with self.assertRaises(PermissionDenied):
            self._sobre_la_persona(autor=otro_pm)

    def test_un_ingeniero_no_observa_a_nadie(self):
        with self.assertRaises(PermissionDenied):
            self._sobre_la_persona(autor=self.ing)

    def test_el_admin_si(self):
        f = self._sobre_la_persona(autor=self.admin)
        self.assertEqual(f.autor, self.admin)


class OpinionSobreElProyectoTests(BaseFeedback):

    def test_la_persona_puede_opinar_de_su_proyecto(self):
        f = self._sobre_el_proyecto()
        self.assertEqual(f.claridad_objetivo, 2)
        self.assertEqual(f.horas_hasta_respuesta, 72)

    def test_sin_puntuar_la_claridad_no_se_guarda(self):
        with self.assertRaises(ValidationError):
            self._sobre_el_proyecto(claridad_objetivo=None)

    def test_la_claridad_va_del_1_al_5(self):
        with self.assertRaises(ValidationError):
            self._sobre_el_proyecto(claridad_objetivo=9)

    def test_nadie_opina_por_otro(self):
        otro_ing = User.objects.create_user("ing2_fb", "i2@test.com", "clave-larga-1")
        otro_ing.groups.add(Group.objects.get(name="Ingeniero"))
        Recurso.objects.create(
            nombre="Tercera", email="i2@test.com", banda="JR", usuario=otro_ing,
        )
        with self.assertRaises(PermissionDenied):
            self._sobre_el_proyecto(autor=otro_ing)


class QuienLeeQueTests(BaseFeedback):
    """La asimetria deliberada, que es la decision de diseno del modulo."""

    def test_la_persona_lee_lo_que_se_dijo_de_ella(self):
        """No hay expediente secreto: lo que se usa para evaluar a alguien tiene
        que poder ser leido por esa persona."""
        f = self._sobre_la_persona()
        self.assertIn(f, svc.feedback_visible(self.ing))

    def test_el_pm_lee_lo_que_el_mismo_observo(self):
        f = self._sobre_la_persona()
        self.assertIn(f, svc.feedback_visible(self.pm))

    def test_el_pm_NO_lee_lo_que_se_dijo_de_su_proyecto(self):
        """El nucleo del asunto. Si el jefe de proyecto pudiera leer esto con
        nombre y apellido, la respuesta honesta —«llevo tres dias esperando»— no
        se escribiria nunca, y el formulario recogeria cortesia."""
        f = self._sobre_el_proyecto()
        self.assertNotIn(f, svc.feedback_visible(self.pm))

    def test_quien_lo_escribio_si_lo_ve(self):
        f = self._sobre_el_proyecto()
        self.assertIn(f, svc.feedback_visible(self.ing))

    def test_el_admin_lo_ve_todo(self):
        """Es quien tiene que actuar: sin ver las dos direcciones no puede
        distinguir un problema de desempeno de uno de entrada."""
        sobre_persona = self._sobre_la_persona()
        sobre_proyecto = self._sobre_el_proyecto()
        visible = svc.feedback_visible(self.admin)
        self.assertIn(sobre_persona, visible)
        self.assertIn(sobre_proyecto, visible)

    def test_un_ingeniero_ajeno_no_ve_nada(self):
        self._sobre_la_persona()
        self._sobre_el_proyecto()
        ajeno = User.objects.create_user("aj_fb", "aj@test.com", "clave-larga-1")
        ajeno.groups.add(Group.objects.get(name="Ingeniero"))
        self.assertEqual(list(svc.feedback_visible(ajeno)), [])


class LaHuellaDeLaEdicionTests(BaseFeedback):
    """No se impide editar —una errata tiene que poder corregirse— pero que se
    note es lo que evita reescribir una observacion despues de que la lean."""

    def test_recien_escrito_no_figura_como_editado(self):
        f = self._sobre_la_persona()
        self.assertFalse(f.editado)

    def test_al_cambiarlo_mas_tarde_si(self):
        from datetime import timedelta

        f = self._sobre_la_persona()
        Feedback.objects.filter(pk=f.pk).update(
            updated_at=f.created_at + timedelta(hours=3)
        )
        f.refresh_from_db()
        self.assertTrue(f.editado)


class LaPantallaDeFeedbackTests(BaseFeedback):

    def test_se_abre_para_cualquiera(self):
        self.client.force_login(self.ing)
        resp = self.client.get(reverse("feedback"))
        self.assertEqual(resp.status_code, 200)

    def test_al_ingeniero_no_se_le_ofrece_observar(self):
        self.client.force_login(self.ing)
        resp = self.client.get(reverse("feedback"))
        self.assertFalse(resp.context["puede_observar"])

    def test_al_pm_si(self):
        self.client.force_login(self.pm)
        resp = self.client.get(reverse("feedback"))
        self.assertTrue(resp.context["puede_observar"])
        self.assertIn(self.recurso, resp.context["recursos_observables"])

    def test_registrar_una_observacion(self):
        self.client.force_login(self.pm)
        self.client.post(reverse("feedback"), {
            "direccion": Feedback.PROYECTO_A_RECURSO,
            "recurso": self.recurso.pk,
            "proyecto": self.proyecto.pk,
            "conducta": "avisa cuando se atasca, y pronto",
            "impacto": "no se pierde el dia",
            "tipo": Feedback.FORTALEZA,
        })
        self.assertEqual(
            Feedback.objects.filter(direccion=Feedback.PROYECTO_A_RECURSO).count(), 1,
        )

    def test_registrar_una_opinion_del_proyecto(self):
        self.client.force_login(self.ing)
        self.client.post(reverse("feedback"), {
            "direccion": Feedback.RECURSO_A_PROYECTO,
            "proyecto": self.proyecto.pk,
            "claridad_objetivo": "2",
            "tuve_que_intuir": "si",
            "que_intui": "el nombre de las tablas",
            "que_ahorraria_tiempo": "un ejemplo de la respuesta esperada",
        })
        f = Feedback.objects.get(direccion=Feedback.RECURSO_A_PROYECTO)
        self.assertEqual(f.claridad_objetivo, 2)
        self.assertTrue(f.tuve_que_intuir)
