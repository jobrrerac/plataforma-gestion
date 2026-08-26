"""Tests del SSO con Entra ID.

No se habla con Entra: el backend recibe las claims ya verificadas, así que los
tests las inyectan directamente. Lo que importa probar aquí es el mapeo de app
roles a grupos de Django, porque de él depende todo el RBAC del proyecto, y la
convivencia con el login local.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts import roles
from apps.accounts.models import CambioPasswordPendiente
from apps.accounts.oidc import EntraOIDCBackend
from apps.core.models import Recurso


def claims(email="ana.perez@inetum.com", nombre="Ana Perez", roles_entra=None, **extra):
    """Claims tal como las emite Entra en el id_token."""
    datos = {
        "email": email,
        "preferred_username": email,
        "name": nombre,
        "oid": "00000000-0000-0000-0000-000000000001",
    }
    if roles_entra is not None:
        datos["roles"] = roles_entra
    datos.update(extra)
    return datos


class MapeoDeRolesTests(TestCase):
    """El claim `roles` de Entra manda sobre los grupos Admin/PM/Ingeniero."""

    @classmethod
    def setUpTestData(cls):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)

    def setUp(self):
        self.backend = EntraOIDCBackend()

    def _grupos(self, user):
        return set(user.groups.values_list("name", flat=True))

    def test_rol_pm_entra_al_grupo_pm(self):
        user = self.backend.create_user(claims(roles_entra=["PM"]))
        self.assertEqual(self._grupos(user), {roles.PM})
        self.assertTrue(roles.puede_ver_costos(user))

    def test_rol_ingeniero_nunca_ve_costos(self):
        user = self.backend.create_user(claims(roles_entra=["Ingeniero"]))
        self.assertEqual(self._grupos(user), {roles.INGENIERO})
        self.assertFalse(roles.puede_ver_costos(user))

    def test_sin_rol_asignado_entra_pero_sin_permisos(self):
        # Fallo seguro: quien no tiene rol en Entra entra sin grupo, y la
        # allowlist de roles.py hace que no vea costos ni datos personales.
        user = self.backend.create_user(claims(roles_entra=[]))
        self.assertEqual(self._grupos(user), set())
        self.assertFalse(roles.puede_ver_costos(user))
        self.assertFalse(roles.puede_ver_datos_personales(user))

    def test_quitar_el_rol_en_entra_quita_el_grupo(self):
        user = self.backend.create_user(claims(roles_entra=["PM"]))
        self.assertEqual(self._grupos(user), {roles.PM})

        # Segundo inicio de sesión: en Entra le degradaron el rol.
        self.backend.update_user(user, claims(roles_entra=["Ingeniero"]))
        self.assertEqual(self._grupos(user), {roles.INGENIERO})
        self.assertFalse(roles.puede_ver_costos(user))

    def test_perder_todos_los_roles_deja_al_usuario_sin_grupos(self):
        user = self.backend.create_user(claims(roles_entra=["Admin"]))
        self.backend.update_user(user, claims(roles_entra=[]))
        self.assertEqual(self._grupos(user), set())
        self.assertFalse(user.is_staff)

    def test_no_toca_grupos_ajenos(self):
        # Un grupo que no gestiona el SSO (creado a mano para otra cosa) debe
        # sobrevivir a la sincronización.
        otro = Group.objects.create(name="ComiteTecnico")
        user = self.backend.create_user(claims(roles_entra=["PM"]))
        user.groups.add(otro)

        self.backend.update_user(user, claims(roles_entra=["Ingeniero"]))
        self.assertEqual(self._grupos(user), {roles.INGENIERO, "ComiteTecnico"})

    def test_rol_desconocido_se_ignora(self):
        user = self.backend.create_user(claims(roles_entra=["Director", "PM"]))
        self.assertEqual(self._grupos(user), {roles.PM})

    def test_admin_recibe_is_staff_pero_nunca_is_superuser(self):
        user = self.backend.create_user(claims(roles_entra=["Admin"]))
        self.assertTrue(user.is_staff)
        # Superusuario se salta todos los permisos: eso no lo concede un token.
        self.assertFalse(user.is_superuser)

    def test_degradar_desde_admin_retira_is_staff(self):
        user = self.backend.create_user(claims(roles_entra=["Admin"]))
        self.backend.update_user(user, claims(roles_entra=["Ingeniero"]))
        user.refresh_from_db()
        self.assertFalse(user.is_staff)


class AltaDeUsuariosTests(TestCase):
    def setUp(self):
        self.backend = EntraOIDCBackend()

    def test_crea_usuario_con_nombre_y_email(self):
        user = self.backend.create_user(claims(email="Ana.Perez@inetum.com", nombre="Ana Perez"))
        self.assertEqual(user.email, "ana.perez@inetum.com")
        self.assertEqual(user.first_name, "Ana")
        self.assertEqual(user.last_name, "Perez")

    def test_usuario_sso_no_tiene_password_utilizable(self):
        user = self.backend.create_user(claims())
        self.assertFalse(user.has_usable_password())

    @override_settings(OIDC_CREAR_USUARIOS=False)
    def test_con_creacion_desactivada_no_da_de_alta(self):
        self.assertIsNone(self.backend.create_user(claims()))
        self.assertFalse(User.objects.filter(email="ana.perez@inetum.com").exists())

    def test_encuentra_usuario_existente_por_email_sin_importar_mayusculas(self):
        existente = User.objects.create_user(username="ana", email="ana.perez@inetum.com")
        encontrados = self.backend.filter_users_by_claims(claims(email="Ana.Perez@INETUM.com"))
        self.assertEqual(list(encontrados), [existente])

    def test_encuentra_usuario_existente_por_username(self):
        # Cuentas del importador masivo: el username es el email pero el campo
        # email puede haber quedado vacío.
        existente = User.objects.create_user(username="ana.perez@inetum.com")
        encontrados = self.backend.filter_users_by_claims(claims())
        self.assertEqual(list(encontrados), [existente])

    def test_token_sin_email_se_rechaza(self):
        sin_email = {"name": "Sin Correo", "oid": "x"}
        self.assertFalse(self.backend.verify_claims(sin_email))

    def test_usa_preferred_username_si_no_hay_email(self):
        sin_email = {"preferred_username": "carlos@inetum.com", "name": "Carlos"}
        self.assertTrue(self.backend.verify_claims(sin_email))
        user = self.backend.create_user(sin_email)
        self.assertEqual(user.email, "carlos@inetum.com")


class EnlaceConRecursoTests(TestCase):
    def setUp(self):
        self.backend = EntraOIDCBackend()

    def test_enlaza_recurso_con_el_mismo_email(self):
        recurso = Recurso.objects.create(
            nombre="Ana Perez", email="ana.perez@inetum.com", banda="SR"
        )
        user = self.backend.create_user(claims(email="ana.perez@inetum.com"))

        recurso.refresh_from_db()
        self.assertEqual(recurso.usuario, user)

    def test_no_pisa_un_recurso_ya_enlazado_a_otro_usuario(self):
        otro = User.objects.create_user(username="otro", email="otro@inetum.com")
        recurso = Recurso.objects.create(
            nombre="Ana Perez", email="ana.perez@inetum.com", banda="SR", usuario=otro
        )

        self.backend.create_user(claims(email="ana.perez@inetum.com"))

        recurso.refresh_from_db()
        self.assertEqual(recurso.usuario, otro)

    def test_sin_recurso_coincidente_no_falla(self):
        user = self.backend.create_user(claims(email="nadie@inetum.com"))
        self.assertIsNotNone(user)


class ConvivenciaConLoginLocalTests(TestCase):
    """El login local debe seguir funcionando con el SSO activado."""

    def setUp(self):
        self.backend = EntraOIDCBackend()

    def test_login_local_sigue_funcionando(self):
        User.objects.create_user(username="local", password="ClaveLocal2026!")
        self.assertTrue(self.client.login(username="local", password="ClaveLocal2026!"))

    def test_sso_levanta_el_cambio_de_password_obligatorio(self):
        # Cuenta creada por el importador masivo con credencial temporal que
        # después entra por SSO: no tiene sentido pedirle cambiar una
        # contraseña que no va a usar, y el formulario le pediría la anterior.
        user = User.objects.create_user(
            username="pm.temporal", email="pm.temporal@inetum.com", password="123"
        )
        CambioPasswordPendiente.objects.create(usuario=user)

        self.backend.update_user(user, claims(email="pm.temporal@inetum.com", roles_entra=["PM"]))

        self.assertFalse(CambioPasswordPendiente.objects.filter(usuario=user).exists())

    def test_usuario_sso_no_queda_atrapado_en_el_cambio_de_password(self):
        user = self.backend.create_user(claims(roles_entra=["PM"]))
        CambioPasswordPendiente.objects.create(usuario=user)

        self.client.force_login(user)
        # El middleware lo deja pasar: sin contraseña utilizable, no hay nada
        # que cambiar.
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)


class RutasSSOTests(TestCase):
    def test_login_sin_sso_no_muestra_el_boton(self):
        html = self.client.get(reverse("login")).content.decode()
        self.assertNotIn("Iniciar sesión con Microsoft", html)

    def test_login_siempre_ofrece_usuario_y_password(self):
        # Aunque el SSO esté activo, el formulario local no desaparece nunca:
        # es la vía de entrada si Entra falla o el secreto de cliente caduca.
        html = self.client.get(reverse("login")).content.decode()
        self.assertIn('name="username"', html)
        self.assertIn('name="password"', html)


class SondasDeSaludTests(TestCase):
    def test_healthz_responde_ok_sin_autenticar(self):
        resp = self.client.get(reverse("healthz"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["estado"], "ok")

    def test_readyz_responde_ok_sin_autenticar(self):
        resp = self.client.get(reverse("readyz"))
        self.assertEqual(resp.status_code, 200)


ALIAS = {"inetumoffshore.onmicrosoft.com": "inetum.com"}


@override_settings(OIDC_DOMINIO_ALIAS=ALIAS)
class AliasDeDominioTests(TestCase):
    """El tenant de Entra usa `@inetumoffshore.onmicrosoft.com`; la plataforma
    conoce a las personas por su email corporativo `@inetum.com`.

    Sin la traducción, cada inicio de sesión por SSO crearía una cuenta nueva y
    dejaría huérfano el historial de asignaciones de la existente. Es el caso
    real de este despliegue, no una hipótesis.
    """

    def setUp(self):
        self.backend = EntraOIDCBackend()

    def _claims_entra(self, alias_local="luisa.acosta-pelaez", **extra):
        upn = f"{alias_local}@inetumoffshore.onmicrosoft.com"
        # Entra manda el UPN en preferred_username; sin buzón no manda `email`.
        return {"preferred_username": upn, "upn": upn, "name": "Luisa Acosta", **extra}

    def test_traduce_el_dominio_al_corporativo(self):
        self.assertEqual(
            self.backend._email(self._claims_entra()),
            "luisa.acosta-pelaez@inetum.com",
        )

    def test_reconoce_al_usuario_existente_en_vez_de_duplicarlo(self):
        existente = User.objects.create_user(
            username="luisa.acosta-pelaez@inetum.com",
            email="luisa.acosta-pelaez@inetum.com",
        )
        encontrados = self.backend.filter_users_by_claims(self._claims_entra())
        self.assertEqual(list(encontrados), [existente])

    def test_el_usuario_existente_conserva_sus_datos_y_su_id(self):
        Group.objects.get_or_create(name=roles.INGENIERO)
        existente = User.objects.create_user(
            username="luisa.acosta-pelaez@inetum.com",
            email="luisa.acosta-pelaez@inetum.com",
        )
        pk_original = existente.pk

        user = self.backend.update_user(
            existente, self._claims_entra(roles=["Ingeniero"])
        )

        self.assertEqual(user.pk, pk_original)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(user.email, "luisa.acosta-pelaez@inetum.com")

    def test_enlaza_el_recurso_por_el_email_corporativo(self):
        recurso = Recurso.objects.create(
            nombre="Luisa Acosta", email="luisa.acosta-pelaez@inetum.com", banda="SSR"
        )
        self.backend.create_user(self._claims_entra())

        recurso.refresh_from_db()
        self.assertIsNotNone(recurso.usuario)

    def test_cuenta_nueva_se_crea_con_el_email_corporativo(self):
        user = self.backend.create_user(self._claims_entra(alias_local="diego.sautter"))
        self.assertEqual(user.email, "diego.sautter@inetum.com")

    def test_tambien_encuentra_cuentas_guardadas_con_el_dominio_de_entra(self):
        # Cuenta creada antes de configurar el alias: arreglar la configuración
        # no debe duplicarla.
        existente = User.objects.create_user(
            username="carlos@inetumoffshore.onmicrosoft.com",
            email="carlos@inetumoffshore.onmicrosoft.com",
        )
        encontrados = self.backend.filter_users_by_claims(
            self._claims_entra(alias_local="carlos")
        )
        self.assertEqual(list(encontrados), [existente])

    def test_un_dominio_sin_alias_se_deja_intacto(self):
        claims_externo = {"preferred_username": "ana@otraempresa.com", "name": "Ana"}
        self.assertEqual(self.backend._email(claims_externo), "ana@otraempresa.com")


class SinAliasConfiguradoTests(TestCase):
    @override_settings(OIDC_DOMINIO_ALIAS={})
    def test_el_email_del_token_se_usa_tal_cual(self):
        backend = EntraOIDCBackend()
        upn = "luisa.acosta-pelaez@inetumoffshore.onmicrosoft.com"
        self.assertEqual(backend._email({"preferred_username": upn}), upn)


ALIAS_ADMIN = {"admin@inetumoffshore.onmicrosoft.com": "inetum_admin"}


@override_settings(OIDC_DOMINIO_ALIAS=ALIAS, OIDC_USUARIO_ALIAS=ALIAS_ADMIN)
class AliasDeUsuarioTests(TestCase):
    """La cuenta administrativa del tenant entra como el superusuario existente.

    `admin@inetumoffshore.onmicrosoft.com` no deriva de ningún email de negocio,
    así que el alias de dominio no basta: hace falta decir explícitamente a qué
    cuenta de Django corresponde.
    """

    @classmethod
    def setUpTestData(cls):
        Group.objects.get_or_create(name=roles.ADMIN)

    def setUp(self):
        self.backend = EntraOIDCBackend()
        self.admin = User.objects.create_user(
            username="inetum_admin",
            email="jose.barrera-cocunubo@inetum.com",
            is_staff=True,
            is_superuser=True,
        )

    def _claims_admin(self, roles_entra=None):
        upn = "admin@inetumoffshore.onmicrosoft.com"
        datos = {"preferred_username": upn, "upn": upn, "name": "Tenant Admin"}
        if roles_entra is not None:
            datos["roles"] = roles_entra
        return datos

    def test_entra_como_el_superusuario_existente(self):
        encontrados = self.backend.filter_users_by_claims(self._claims_admin())
        self.assertEqual(list(encontrados), [self.admin])

    def test_no_crea_una_cuenta_nueva(self):
        self.backend.update_user(self.admin, self._claims_admin(["Admin"]))
        self.assertEqual(User.objects.count(), 1)

    def test_conserva_el_email_de_negocio(self):
        # Sin esta protección, el token pisaría el email real del superusuario
        # con "admin@inetum.com".
        self.backend.update_user(self.admin, self._claims_admin(["Admin"]))
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.email, "jose.barrera-cocunubo@inetum.com")

    def test_conserva_is_superuser(self):
        self.backend.update_user(self.admin, self._claims_admin(["Admin"]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser)
        self.assertTrue(self.admin.is_staff)

    def test_un_superusuario_no_pierde_is_staff_al_perder_el_rol(self):
        # Quitarle is_staff dejaría al administrador fuera de su propio /admin/,
        # y recuperarlo exigiría acceso directo a la base de datos.
        self.backend.update_user(self.admin, self._claims_admin([]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)

    def test_si_el_alias_apunta_a_una_cuenta_inexistente_se_deniega(self):
        self.admin.delete()
        self.assertEqual(list(self.backend.filter_users_by_claims(self._claims_admin())), [])
        self.assertIsNone(self.backend.create_user(self._claims_admin()))
        self.assertEqual(User.objects.count(), 0)

    def test_los_demas_usuarios_no_se_ven_afectados(self):
        otro = User.objects.create_user(
            username="luisa.acosta-pelaez@inetum.com", email="luisa.acosta-pelaez@inetum.com"
        )
        upn = "luisa.acosta-pelaez@inetumoffshore.onmicrosoft.com"
        encontrados = self.backend.filter_users_by_claims({"preferred_username": upn})
        self.assertEqual(list(encontrados), [otro])
