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
        # Logout no debe quedar interceptado por el middleware hacia la página de cambio.
        resp = self.client.get(reverse("logout"))
        destino = resp.url if resp.status_code == 302 else ""
        self.assertNotEqual(destino, reverse("password-cambiar"))

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
