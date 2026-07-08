from django.contrib.auth.models import AnonymousUser, Group, User
from django.test import TestCase

from apps.accounts import roles


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
