"""El dashboard de un proyecto.

El de ocupación responde «quién está libre». Este responde la otra pregunta, la
de la reunión de seguimiento: **cómo va este proyecto**. Junta el plan, lo
declarado, lo que lo frena y —solo para el Admin— lo que dice de él la gente que
trabaja dentro.

Lo que se blinda aquí: que no filtre a quien no debe verlo, y que la señal más
delicada —lo que el equipo dijo de las condiciones del proyecto— no se le escape
al jefe de ese proyecto, que también entra a esta pantalla.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.legalizacion.models import DiaLegalizado, RegistroHoras, TipoActividad
from apps.seguimiento import services as seg
from apps.seguimiento.models import Feedback


class BaseProyecto(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm_dp", "adp@test.com", "clave-larga-1")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.pm = User.objects.create_user("pm_dp", "pdp@test.com", "clave-larga-1")
        self.pm.groups.add(Group.objects.get_or_create(name="PM")[0])
        self.ing = User.objects.create_user("ing_dp", "idp@test.com", "clave-larga-1")
        self.ing.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])

        self.hoy = timezone.localdate()
        self.recurso = Recurso.objects.create(
            nombre="Trabajadora Diaz", email="idp@test.com", banda="JR", usuario=self.ing,
        )
        self.proyecto = Proyecto.objects.create(
            codigo="DP-01", nombre="Proyecto medido", cliente="ACME", pm=self.pm,
            fecha_inicio=self.hoy - timedelta(days=30),
            fecha_fin=self.hoy + timedelta(days=30),
        )
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=self.hoy - timedelta(days=20),
            fecha_fin=self.hoy + timedelta(days=20),
            horas_totales=100, intensidad_diaria=8.5,
            estado="APROBADA", solicitada_por=self.pm,
        )
        self.tipo = TipoActividad.objects.create(nombre="Proyecto DP", requiere_proyecto=True)

    def _horas(self, horas="8.5", dias_atras=1):
        dia, _ = DiaLegalizado.objects.get_or_create(
            recurso=self.recurso, fecha=self.hoy - timedelta(days=dias_atras),
            defaults={"total_horas": Decimal(horas), "jornada_esperada": Decimal("8.5")},
        )
        return RegistroHoras.objects.create(
            dia=dia, tipo_actividad=self.tipo, proyecto=self.proyecto,
            horas=Decimal(horas), detalle="lo que hizo ese dia",
            estado=RegistroHoras.APROBADO, aprobado_por=self.pm,
            aprobado_en=timezone.now(),
        )

    def _ver(self, usuario, **params):
        self.client.force_login(usuario)
        datos = {"proyecto": self.proyecto.pk}
        datos.update(params)
        resp = self.client.get(reverse("dashboard-proyecto"), datos)
        self.assertEqual(resp.status_code, 200)
        return resp


class QuienPuedeEntrarTests(BaseProyecto):

    def test_el_admin_si(self):
        self._ver(self.admin)

    def test_el_pm_si(self):
        self._ver(self.pm)

    def test_el_ingeniero_no(self):
        """Resume el trabajo de otras personas: no es una pantalla para quien
        solo se ve a si mismo."""
        self.client.force_login(self.ing)
        resp = self.client.get(reverse("dashboard-proyecto"))
        self.assertEqual(resp.status_code, 403)

    def test_sin_sesion_tampoco(self):
        resp = self.client.get(reverse("dashboard-proyecto"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_sin_elegir_proyecto_no_revienta(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("dashboard-proyecto"))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["proyecto"])


class LoQueResumeTests(BaseProyecto):

    def test_cuenta_las_horas_aprobadas(self):
        self._horas("8.5", 1)
        self._horas("4", 2)
        ctx = self._ver(self.admin).context
        self.assertEqual(ctx["horas"], Decimal("12.5"))
        self.assertEqual(ctx["actividades"], 2)

    def test_solo_cuenta_lo_aprobado(self):
        """Lo pendiente todavia puede cambiar. Sumarlo daria un avance que no
        ha firmado nadie."""
        r = self._horas("8.5", 1)
        RegistroHoras.objects.filter(pk=r.pk).update(estado=RegistroHoras.PENDIENTE)
        self.assertEqual(self._ver(self.admin).context["horas"], Decimal("0"))

    def test_agrupa_por_persona(self):
        self._horas("8.5", 1)
        ctx = self._ver(self.admin).context
        fila = ctx["por_recurso"][0]
        self.assertEqual(fila["recurso"], self.recurso)
        self.assertEqual(fila["horas"], Decimal("8.5"))
        self.assertEqual(fila["ultimo"], self.hoy - timedelta(days=1))

    def test_sale_quien_esta_asignado_aunque_no_haya_declarado_nada(self):
        """Es justo el caso que interesa ver: alguien asignado que no ha tocado
        el proyecto. Si solo se listara a quien declaro horas, desaparece."""
        ctx = self._ver(self.admin).context
        self.assertEqual(len(ctx["por_recurso"]), 1)
        self.assertIsNone(ctx["por_recurso"][0]["ultimo"])

    def test_cuenta_los_bloqueantes_del_proyecto(self):
        seg.abrir_bloqueante(
            self.recurso, self.ing, "el esquema",
            rol_que_resuelve="PM", proyecto=self.proyecto,
        )
        ctx = self._ver(self.admin).context
        self.assertEqual(len(ctx["bloqueantes_abiertos"]), 1)
        self.assertEqual(ctx["por_recurso"][0]["bloqueantes"], 1)

    def test_la_media_de_desbloqueo_solo_mira_los_cerrados(self):
        """Los abiertos siguen contando: meterlos bajaria la media justo cuando
        peor va la cosa."""
        b = seg.abrir_bloqueante(
            self.recurso, self.ing, "uno", rol_que_resuelve="PM", proyecto=self.proyecto,
        )
        seg.resolver_bloqueante(b, self.ing)
        seg.abrir_bloqueante(
            self.recurso, self.ing, "otro abierto",
            rol_que_resuelve="PM", proyecto=self.proyecto,
        )
        ctx = self._ver(self.admin).context
        self.assertIsNotNone(ctx["media_desbloqueo"])
        self.assertEqual(len(ctx["bloqueantes_resueltos"]), 1)

    def test_avisa_si_las_asignaciones_pasan_del_fin_del_proyecto(self):
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=self.hoy, fecha_fin=self.proyecto.fecha_fin + timedelta(days=15),
            horas_totales=40, intensidad_diaria=4,
            estado="APROBADA", solicitada_por=self.pm,
        )
        ctx = self._ver(self.admin).context
        self.assertGreater(ctx["fin_asignaciones"], self.proyecto.fecha_fin)


class LaSenalDelicadaTests(BaseProyecto):
    """Lo que el equipo dijo de las condiciones del proyecto.

    Se pidió con la promesa de que solo lo leyera el manager en Colombia. Esta
    pantalla la abre también el jefe de ese proyecto, así que aquí es donde esa
    promesa se rompería con más facilidad.
    """

    def setUp(self):
        super().setUp()
        seg.registrar_feedback(
            recurso=self.recurso, autor=self.ing,
            direccion=Feedback.RECURSO_A_PROYECTO,
            proyecto=self.proyecto, claridad_objetivo=2,
            comentario="el alcance llegaba a medias",
        )

    def test_el_admin_ve_la_claridad_media(self):
        ctx = self._ver(self.admin).context
        self.assertEqual(ctx["claridad"]["media"], 2)
        self.assertEqual(ctx["claridad"]["cuantos"], 1)

    def test_el_pm_no(self):
        self.assertIsNone(self._ver(self.pm).context["claridad"])

    def test_y_no_se_le_cuela_en_el_html(self):
        """Ni el numero ni la etiqueta: si la tarjeta se pintara vacia, el PM
        sabria que existe y quien la escribio."""
        html = self._ver(self.pm).content.decode()
        self.assertNotIn("Claridad del objetivo", html)
        self.assertNotIn("el alcance llegaba a medias", html)
