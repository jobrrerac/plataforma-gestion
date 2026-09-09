"""Los dos concentradores de navegación.

La barra superior llegó a once pestañas y dejó de caber. El problema no era el
ancho: mezclaba en una sola fila lo que uno **reporta sobre sí mismo** y lo que
**revisa de otros**, así que había que leerla entera para encontrar cualquier
cosa.

Lo que se blinda aquí es que el agrupamiento no acabe enseñándole a nadie
acciones que no puede ejecutar. Un menú con enlaces que devuelven 403 enseña a
desconfiar del menú entero, y entonces la gente deja de usarlo y pide las URL por
chat — que es exactamente de donde veníamos.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Proyecto, Recurso


class BaseHub(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm_hub", "ah@test.com", "clave-larga-1")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.pm = User.objects.create_user("pm_hub", "ph@test.com", "clave-larga-1")
        self.pm.groups.add(Group.objects.get_or_create(name="PM")[0])
        self.ing = User.objects.create_user("ing_hub", "ih@test.com", "clave-larga-1")
        self.ing.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])
        Recurso.objects.create(
            nombre="Navegante Ruiz", email="ih@test.com", banda="JR", usuario=self.ing,
        )

    def _urls(self, usuario, ruta):
        self.client.force_login(usuario)
        resp = self.client.get(reverse(ruta))
        self.assertEqual(resp.status_code, 200)
        return {i["url"] for i in resp.context["items"]}


class RegistrarTests(BaseHub):
    """Lo que uno reporta sobre su propio trabajo. Es de todos."""

    def test_un_ingeniero_ve_solo_lo_suyo(self):
        urls = self._urls(self.ing, "registrar")
        self.assertEqual(
            urls, {"/horas/", "/novedades/", "/bloqueantes/", "/feedback/"},
        )

    def test_un_pm_registra_lo_mismo_que_todos(self):
        """Un PM tambien legaliza su tiempo y tambien tiene vacaciones. Lo que
        pide sobre el equipo vive en Solicitar, que es otra cosa."""
        self.assertEqual(len(self._urls(self.pm, "registrar")), 4)

    def test_solicitar_no_esta_aqui(self):
        """Registrar es contar lo que ya paso; solicitar es pedir algo que
        todavia no existe y que alguien tiene que aprobar."""
        self.assertNotIn("/solicitud/", self._urls(self.pm, "registrar"))


class SolicitarTests(BaseHub):
    """Lo que se pide sobre el equipo: gente, horas y fechas."""

    def test_un_pm_ve_las_tres(self):
        self.assertEqual(
            self._urls(self.pm, "solicitar"),
            {"/solicitud/", "/cesion/", "/liberacion/"},
        )

    def test_un_ingeniero_no_ve_ninguna(self):
        """La vista lo bloquea con `PMOAdminRequiredMixin`; ofrecerselo seria
        ofrecerle un 403."""
        self.assertEqual(self._urls(self.ing, "solicitar"), set())

    def test_sin_sesion_no(self):
        resp = self.client.get(reverse("registrar"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])


class GestionarTests(BaseHub):
    """Lo que se revisa o aprueba del trabajo de otros. Depende del rol."""

    def test_el_admin_lo_ve_todo(self):
        urls = self._urls(self.admin, "gestionar")
        for esperada in (
            "/horas/aprobar/", "/novedades/revisar/",
            "/bloqueantes/equipo/", "/feedback/equipo/",
        ):
            self.assertIn(esperada, urls)

    def test_solicitar_no_esta_aqui(self):
        """Solicitar es registrar; aprobar es gestionar. Mezclarlos es lo que
        hizo que la barra creciera hasta no caber."""
        urls = self._urls(self.admin, "gestionar")
        self.assertNotIn("/solicitud/", urls)

    def test_el_pm_no_ve_lo_que_es_solo_de_admin(self):
        urls = self._urls(self.pm, "gestionar")
        self.assertIn("/horas/aprobar/", urls)
        self.assertNotIn(
            "/novedades/revisar/", urls,
            "revisar novedades es de Admin; ofrecerselo a un PM es ofrecerle un 403",
        )

    def test_a_un_ingeniero_no_se_le_ofrece_nada(self):
        """No es un 403 a propósito: la pantalla explica que esto es de quien
        aprueba o planifica. Un 403 seco parece un fallo de permisos."""
        self.assertEqual(self._urls(self.ing, "gestionar"), set())

    def test_un_aprobador_delegado_si_ve_aprobar_horas(self):
        """Aprobar horas no depende del rol: un proyecto puede designar a
        cualquiera. Si el enlace no le sale, tiene que saberse la URL."""
        from datetime import date

        visor = User.objects.create_user("del_hub", "dh@test.com", "clave-larga-1")
        visor.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])
        Proyecto.objects.create(
            codigo="HUB-01", nombre="Con delegado", pm=self.pm,
            aprobador_delegado=visor,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
        )
        self.assertIn("/horas/aprobar/", self._urls(visor, "gestionar"))


class LaBarraSuperiorTests(BaseHub):

    def test_no_vuelve_a_llenarse(self):
        """Cuatro verbos como mucho: Dashboard, Registrar, Solicitar, Gestionar.
        Si esto crece, es que se volvio a colgar cada pantalla nueva de la barra
        en vez de meterla en su concentrador."""
        self.client.force_login(self.admin)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertLessEqual(
            html.count('class="inet-nav-link'), 4,
            "la barra volvio a llenarse: cada pantalla nueva va en un hub",
        )

    def test_no_hay_atajos_sueltos_al_admin(self):
        """Habia un enlace directo a /admin/assignments/asignacion/. Un menu que
        mezcla secciones de la aplicacion con atajos al panel de Django deja de
        tener un criterio que se pueda explicar, y el admin ya esta a la derecha."""
        self.client.force_login(self.admin)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertNotIn("/admin/assignments/asignacion/", html)

    def test_al_ingeniero_solo_se_le_ofrece_registrar(self):
        """Un enlace que no lleva a nada enseña a desconfiar del menú."""
        self.client.force_login(self.ing)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn('href="/registrar/"', html)
        self.assertNotIn('href="/gestionar/"', html)
        self.assertNotIn('href="/solicitar/"', html)

    def test_al_pm_las_tres(self):
        self.client.force_login(self.pm)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn('href="/registrar/"', html)
        self.assertIn('href="/solicitar/"', html)
        self.assertIn('href="/gestionar/"', html)


class ElVisorMiraYNoEscribeTests(BaseHub):
    """El Visor no solicita recursos, pero sí ve lo que pasa con cada persona.

    Es exactamente su papel en el proyecto. Antes veía el enlace de Solicitudes
    en la barra y recibía un 403 al pulsarlo, porque la barra lo gobernaba con
    `puede_ver_todo` y la vista con `es_admin_o_pm`.
    """

    def setUp(self):
        super().setUp()
        self.visor = User.objects.create_user("vis_hub", "vh@test.com", "clave-larga-1")
        self.visor.groups.add(Group.objects.get_or_create(name="Visor")[0])

    def test_no_puede_solicitar_recursos(self):
        self.assertEqual(self._urls(self.visor, "solicitar"), set())

    def test_pero_si_ve_bloqueantes_y_feedback_del_equipo(self):
        urls = self._urls(self.visor, "gestionar")
        self.assertIn("/bloqueantes/equipo/", urls)
        self.assertIn("/feedback/equipo/", urls)

    def test_y_no_puede_aprobar_nada(self):
        urls = self._urls(self.visor, "gestionar")
        self.assertNotIn("/horas/aprobar/", urls)
        self.assertNotIn("/novedades/revisar/", urls)
