from django.contrib.auth.models import AnonymousUser, Group, User
from django.test import TestCase
from django.urls import reverse

from apps.accounts import roles
from apps.accounts.models import CambioPasswordPendiente


class RolesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)

    def _user(self, username, grupo=None, superuser=False):
        user = User.objects.create_user(username=username, password="x")
        if superuser:
            user.is_superuser = True
            user.save(update_fields=["is_superuser"])
        if grupo:
            user.groups.add(Group.objects.get(name=grupo))
        return user

    def test_admin_ve_costos(self):
        self.assertTrue(roles.puede_ver_costos(self._user("admin1", roles.ADMIN)))

    def test_pm_ve_costos(self):
        self.assertTrue(roles.puede_ver_costos(self._user("pm1", roles.PM)))

    def test_superusuario_ve_costos(self):
        self.assertTrue(roles.puede_ver_costos(self._user("root1", superuser=True)))

    def test_ingeniero_no_ve_costos(self):
        self.assertFalse(roles.puede_ver_costos(self._user("ing1", roles.INGENIERO)))

    def test_usuario_sin_grupo_no_ve_costos(self):
        # Regresión: la lógica antigua era denylist ("no es Ingeniero") y un
        # usuario sin grupo veía costos. Debe ser allowlist (solo Admin/PM).
        self.assertFalse(roles.puede_ver_costos(self._user("sin_grupo")))

    def test_anonimo_no_ve_costos(self):
        self.assertFalse(roles.puede_ver_costos(AnonymousUser()))

    def test_es_admin(self):
        self.assertTrue(roles.es_admin(self._user("admin2", roles.ADMIN)))
        self.assertFalse(roles.es_admin(self._user("pm2", roles.PM)))
        self.assertFalse(roles.es_admin(AnonymousUser()))

    def test_datos_personales_solo_admin_pm(self):
        self.assertTrue(roles.puede_ver_datos_personales(self._user("pm3", roles.PM)))
        self.assertFalse(roles.puede_ver_datos_personales(self._user("ing3", roles.INGENIERO)))
        self.assertFalse(roles.puede_ver_datos_personales(self._user("sin_grupo3")))


class ForzarCambioPasswordTests(TestCase):
    """Flujo de contraseña temporal: el usuario con cambio pendiente queda
    encerrado en la página de cambio hasta que la actualiza."""

    def setUp(self):
        self.user = User.objects.create_user(username="pm.temporal", password="123")
        CambioPasswordPendiente.objects.create(usuario=self.user)
        self.client.login(username="pm.temporal", password="123")

    def test_con_pendiente_redirige_a_cambio(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertRedirects(resp, reverse("password-cambiar"), fetch_redirect_response=False)

    def test_pagina_de_cambio_no_se_bloquea_a_si_misma(self):
        # No debe entrar en bucle de redirección sobre su propia URL.
        resp = self.client.get(reverse("password-cambiar"))
        self.assertEqual(resp.status_code, 200)

    def test_logout_permitido_con_pendiente(self):
        # Logout no debe quedar interceptado por el middleware hacia la página
        # de cambio. Se comprueba que la sesión SE CIERRA, no solo que el
        # destino no sea el formulario: la versión anterior de este test tomaba
        # el destino como cadena vacía cuando la respuesta no era 302, así que
        # pasaba igual aunque el logout devolviera un 405 y no cerrara nada.
        resp = self.client.post(reverse("logout"))
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_cambio_exitoso_borra_flag_y_libera(self):
        resp = self.client.post(reverse("password-cambiar"), {
            "old_password": "123",
            "new_password1": "NuevaClave2026!",
            "new_password2": "NuevaClave2026!",
        })
        self.assertRedirects(resp, reverse("dashboard"), fetch_redirect_response=False)
        self.assertFalse(CambioPasswordPendiente.objects.filter(usuario=self.user).exists())
        # Ya sin flag, el dashboard es accesible.
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_sin_pendiente_no_redirige(self):
        CambioPasswordPendiente.objects.filter(usuario=self.user).delete()
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)


class LogoutTests(TestCase):
    """Cerrar sesión tiene que cerrarla de verdad.

    Regresión reportada en QA: el botón de la cabecera era un enlace, así que
    hacía GET. Desde Django 4.1 `LogoutView` rechaza GET con un 405 — y con
    razón: por GET, una página externa podría cerrar la sesión de cualquiera
    con una simple etiqueta `<img>`. El resultado era un error 405 y la sesión
    intacta.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="sale", password="Clave2026!")
        self.client.force_login(self.user)

    def test_por_post_cierra_la_sesion(self):
        resp = self.client.post(reverse("logout"))
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_por_get_no_cierra_nada(self):
        # Es el comportamiento correcto de Django, no un fallo: se comprueba
        # para que nadie "arregle" el 405 permitiendo GET.
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_la_cabecera_ofrece_un_formulario_no_un_enlace(self):
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn('action="/logout/"', html)
        self.assertNotIn('href="/logout/"', html)

    def test_tras_salir_las_paginas_piden_login(self):
        # Lo que reportó QA: volver a entrar y seguir dentro.
        self.client.post(reverse("logout"))
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)
