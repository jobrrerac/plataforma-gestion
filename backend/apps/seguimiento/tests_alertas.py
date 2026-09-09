"""Las alertas: lo único que convierte esto en seguimiento y no en archivo.

Un registro que nadie mira no sirve de nada. Estas tres alertas son la razón de
ser del módulo, y cada una cubre una forma distinta de que a alguien se le pase
un mes sin que nadie se entere:

- alguien lleva días esperando y no lo sabe nadie,
- alguien tiene asignación pero no está trabajando en ella,
- alguien lleva dos semanas sin que su proyecto diga una palabra sobre él.

La tercera es la más silenciosa y la que ya se nos pasó una vez: el primer
síntoma de abandono no es una queja, es que no hay nada.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.legalizacion.models import DiaLegalizado, RegistroHoras, TipoActividad
from apps.seguimiento import services as svc
from apps.seguimiento.models import HORAS_PARA_ESCALAR, Bloqueante, Feedback


class BaseAlertas(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm_al", "aa@test.com", "clave-larga-1")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.pm = User.objects.create_user("pm_al", "pa@test.com", "clave-larga-1")
        self.pm.groups.add(Group.objects.get_or_create(name="PM")[0])
        self.ing = User.objects.create_user("ing_al", "ia@test.com", "clave-larga-1")
        self.ing.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])

        self.hoy = timezone.localdate()
        self.recurso = Recurso.objects.create(
            nombre="Vigilada Ruiz", email="ia@test.com", banda="JR", usuario=self.ing,
        )
        self.proyecto = Proyecto.objects.create(
            codigo="AL-01", nombre="Proyecto vigilado", pm=self.pm,
            fecha_inicio=self.hoy - timedelta(days=60),
            fecha_fin=self.hoy + timedelta(days=60),
        )
        # `Proyecto.pm` no admite nulos: hasta un proyecto interno tiene dueno.
        self.otro = Proyecto.objects.create(
            codigo="AL-INT", nombre="Interno", facturable=False, pm=self.admin,
            fecha_inicio=self.hoy - timedelta(days=60),
            fecha_fin=self.hoy + timedelta(days=60),
        )
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=self.hoy - timedelta(days=30),
            fecha_fin=self.hoy + timedelta(days=30),
            horas_totales=200, intensidad_diaria=8.5,
            estado="APROBADA", solicitada_por=self.pm,
        )
        self.tipo = TipoActividad.objects.create(nombre="Proyecto AL", requiere_proyecto=True)

    def _imputa(self, dias_atras, proyecto):
        fecha = self.hoy - timedelta(days=dias_atras)
        dia, _ = DiaLegalizado.objects.get_or_create(
            recurso=self.recurso, fecha=fecha,
            defaults={"total_horas": Decimal("8.5"), "jornada_esperada": Decimal("8.5")},
        )
        RegistroHoras.objects.create(
            dia=dia, tipo_actividad=self.tipo, proyecto=proyecto,
            horas=Decimal("8.5"), detalle="lo que hizo ese dia",
        )
        return dia

    def _bloqueante_viejo(self, horas):
        b = svc.abrir_bloqueante(
            self.recurso, self.ing, "el esquema",
            rol_que_resuelve="PM", proyecto=self.proyecto,
        )
        Bloqueante.objects.filter(pk=b.pk).update(
            creado_en=timezone.now() - timedelta(hours=horas)
        )
        return b

    def _tipos(self, usuario=None):
        return [a["tipo"] for a in svc.alertas(usuario or self.admin)]


class BloqueanteVencidoTests(BaseAlertas):

    def test_pasado_el_plazo_aparece(self):
        self._bloqueante_viejo(HORAS_PARA_ESCALAR + 5)
        self.assertIn("BLOQUEANTE", self._tipos())

    def test_antes_del_plazo_no(self):
        self._bloqueante_viejo(HORAS_PARA_ESCALAR - 5)
        self.assertNotIn("BLOQUEANTE", self._tipos())

    def test_resuelto_deja_de_alertar(self):
        b = self._bloqueante_viejo(HORAS_PARA_ESCALAR + 5)
        svc.resolver_bloqueante(b, self.ing)
        self.assertNotIn("BLOQUEANTE", self._tipos())

    def test_la_alerta_dice_a_quien_reclamarle(self):
        self._bloqueante_viejo(HORAS_PARA_ESCALAR + 5)
        alerta = next(a for a in svc.alertas(self.admin) if a["tipo"] == "BLOQUEANTE")
        self.assertIn(self.pm.username, alerta["texto"])


class SinTareaTests(BaseAlertas):
    """«Si alguien no esta recibiendo trabajo, somos nosotros los que nos tenemos
    que dar cuenta primero.» Sale de cruzar el plan con lo declarado, sin ningun
    dato nuevo."""

    def test_dos_dias_imputando_a_otro_lado_alertan(self):
        self._imputa(1, self.otro)
        self._imputa(2, self.otro)
        self.assertIn("SIN_TAREA", self._tipos())

    def test_un_solo_dia_no(self):
        """Un dia suelto es ruido: una formacion, un dia de soporte."""
        self._imputa(1, self.otro)
        self.assertNotIn("SIN_TAREA", self._tipos())

    def test_imputando_a_su_proyecto_no_alerta(self):
        self._imputa(1, self.proyecto)
        self._imputa(2, self.proyecto)
        self.assertNotIn("SIN_TAREA", self._tipos())

    def test_un_dia_mixto_cuenta_como_trabajado(self):
        """Si ese dia toco su proyecto aunque fuera un rato, no esta abandonado."""
        dia = self._imputa(1, self.otro)
        RegistroHoras.objects.create(
            dia=dia, tipo_actividad=self.tipo, proyecto=self.proyecto,
            horas=Decimal("2"), detalle="algo del proyecto",
        )
        self._imputa(2, self.otro)
        self.assertNotIn("SIN_TAREA", self._tipos())

    def test_lo_viejo_queda_fuera_de_la_ventana(self):
        self._imputa(svc.DIAS_VENTANA_SIN_TAREA + 3, self.otro)
        self._imputa(svc.DIAS_VENTANA_SIN_TAREA + 4, self.otro)
        self.assertNotIn("SIN_TAREA", self._tipos())

    def test_sin_asignacion_activa_no_hay_nada_que_reclamar(self):
        Asignacion.objects.all().update(estado="CERRADA")
        self._imputa(1, self.otro)
        self._imputa(2, self.otro)
        self.assertNotIn("SIN_TAREA", self._tipos())


class SinFeedbackTests(BaseAlertas):

    def test_sin_ninguna_observacion_alerta(self):
        self.assertIn("SIN_FEEDBACK", self._tipos())

    def test_con_una_observacion_reciente_no(self):
        svc.registrar_feedback(
            recurso=self.recurso, autor=self.pm,
            direccion=Feedback.PROYECTO_A_RECURSO,
            proyecto=self.proyecto,
            conducta="avisa cuando se atasca", impacto="no se pierde el dia",
            tipo=Feedback.FORTALEZA,
        )
        self.assertNotIn("SIN_FEEDBACK", self._tipos())

    def test_una_observacion_vieja_no_cuenta(self):
        f = svc.registrar_feedback(
            recurso=self.recurso, autor=self.pm,
            direccion=Feedback.PROYECTO_A_RECURSO,
            proyecto=self.proyecto,
            conducta="algo", impacto="algo", tipo=Feedback.FORTALEZA,
        )
        Feedback.objects.filter(pk=f.pk).update(
            fecha_observacion=self.hoy - timedelta(days=svc.DIAS_SIN_FEEDBACK_PARA_ALERTAR + 2)
        )
        self.assertIn("SIN_FEEDBACK", self._tipos())

    def test_lo_que_la_persona_dijo_del_proyecto_no_cuenta_como_observacion(self):
        """Son cosas distintas: que el recurso hable no significa que alguien lo
        haya mirado a el. Confundirlas apagaria la alerta justo cuando mas sirve."""
        svc.registrar_feedback(
            recurso=self.recurso, autor=self.ing,
            direccion=Feedback.RECURSO_A_PROYECTO,
            proyecto=self.proyecto, claridad_objetivo=3,
        )
        self.assertIn("SIN_FEEDBACK", self._tipos())


class QuienVeLasAlertasTests(BaseAlertas):

    def test_solo_quien_puede_actuar(self):
        """Ensenarle a un ingeniero que tres companeros estan sin tarea no le
        sirve de nada y le cuenta cosas que no le corresponden."""
        self._bloqueante_viejo(HORAS_PARA_ESCALAR + 5)
        self.assertEqual(svc.alertas(self.ing), [])
        self.assertEqual(svc.alertas(self.pm), [])
        self.assertNotEqual(svc.alertas(self.admin), [])

    def test_lo_urgente_va_primero(self):
        self._bloqueante_viejo(HORAS_PARA_ESCALAR + 5)
        alertas = svc.alertas(self.admin)
        gravedades = [a["gravedad"] for a in alertas]
        self.assertEqual(gravedades, sorted(gravedades, key=lambda g: {"alta": 0, "media": 1}[g]))

    def test_cada_alerta_dice_que_hacer(self):
        """Una alerta que no dice que hacer se ignora a la segunda semana."""
        self._bloqueante_viejo(HORAS_PARA_ESCALAR + 5)
        for alerta in svc.alertas(self.admin):
            self.assertTrue(alerta["accion"], f"{alerta['tipo']} no dice que hacer")
