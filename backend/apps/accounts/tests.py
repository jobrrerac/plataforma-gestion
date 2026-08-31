from django.contrib.auth.models import AnonymousUser, Group, User
from django.core.cache import cache
from django.test import TestCase, override_settings
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


class BloqueoPorIntentosTests(TestCase):
    """El bloqueo por intentos fallidos tiene que ser de la cuenta, no de todos.

    BUG-AUT-002, reportado en QA: mientras `qa.aut` estaba bloqueada por
    intentos fallidos, `testaut@inetum.com` tampoco podía entrar.

    La causa era peor que una molestia. Se contaba en paralelo por cuenta y por
    IP, y se bloqueaba si cualquiera de los dos contadores llegaba al límite. Y
    la "IP" no era la de nadie: detrás del ingress de Azure, el último valor de
    X-Forwarded-For es a menudo la dirección interna del propio proxy. En el
    cache de producción había una clave `login_fail_ip_100.100.0.31` —una
    dirección privada de Azure— compartida por todas las peticiones.

    Es decir: cinco intentos fallidos de cualquiera dejaban la aplicación
    inaccesible para toda la empresa durante quince minutos, y cualquiera podía
    provocarlo a propósito sin ninguna credencial.
    """

    def setUp(self):
        cache.clear()
        self.victima = User.objects.create_user(
            username="qa.aut@inetum.com", password="Correcta2026!"
        )
        self.tercero = User.objects.create_user(
            username="testaut@inetum.com", password="Correcta2026!"
        )

    def tearDown(self):
        cache.clear()

    def _fallar(self, usuario, veces):
        for _ in range(veces):
            self.client.post(reverse("login"), {"username": usuario, "password": "mala"})

    def test_tras_cinco_fallos_se_bloquea_esa_cuenta(self):
        self._fallar("qa.aut@inetum.com", 5)
        resp = self.client.post(reverse("login"), {
            "username": "qa.aut@inetum.com", "password": "Correcta2026!",
        })
        self.assertEqual(resp.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_el_bloqueo_de_una_cuenta_no_afecta_a_otra(self):
        """El caso reportado, tal cual."""
        self._fallar("qa.aut@inetum.com", 5)
        resp = self.client.post(reverse("login"), {
            "username": "testaut@inetum.com", "password": "Correcta2026!",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_fallar_con_una_cuenta_no_gasta_intentos_de_otra(self):
        # Cuatro fallos ajenos no pueden dejar a nadie a un intento del bloqueo.
        self._fallar("qa.aut@inetum.com", 4)
        self._fallar("testaut@inetum.com", 4)
        resp = self.client.post(reverse("login"), {
            "username": "testaut@inetum.com", "password": "Correcta2026!",
        })
        self.assertEqual(resp.status_code, 302)

    def test_entrar_bien_reinicia_el_contador(self):
        self._fallar("testaut@inetum.com", 4)
        self.client.post(reverse("login"), {
            "username": "testaut@inetum.com", "password": "Correcta2026!",
        })
        self.client.post(reverse("logout"))
        self._fallar("testaut@inetum.com", 4)
        resp = self.client.post(reverse("login"), {
            "username": "testaut@inetum.com", "password": "Correcta2026!",
        })
        self.assertEqual(resp.status_code, 302)

    def test_el_aviso_sale_dentro_del_formulario_y_no_como_pagina_de_error(self):
        # Antes era un 403 en texto plano sobre la pagina en blanco: parecia una
        # caida de la aplicacion, no un limite con su motivo y su plazo.
        self._fallar("qa.aut@inetum.com", 5)
        resp = self.client.post(reverse("login"), {
            "username": "qa.aut@inetum.com", "password": "Correcta2026!",
        })
        html = resp.content.decode()
        self.assertIn("Plataforma de", html)
        self.assertIn("15 minutos", html)

    def test_ya_no_existe_un_contador_por_ip(self):
        """Guarda explícita: reintroducirlo devuelve el bloqueo global.

        Detrás del ingress de Azure la IP observada es la del proxy, así que un
        contador por IP no limita a un atacante: es un interruptor compartido.
        """
        self._fallar("qa.aut@inetum.com", 5)
        claves_ip = [k for k in ("login_fail_ip_127.0.0.1", "login_fail_ip_") if cache.get(k)]
        self.assertEqual(claves_ip, [])


@override_settings(
    SESSION_COOKIE_AGE=3600,
    SESSION_SAVE_EVERY_REQUEST=True,
    SESSION_EXPIRE_AT_BROWSER_CLOSE=True,
)
class SesionPorInactividadTests(TestCase):
    """La sesión caduca por inactividad, no por reloj.

    Antes eran 8 horas contadas desde el login: lo peor de los dos mundos.
    Demasiado para una pantalla que alguien deja abierta y se va, y a la vez
    echaba a quien llevaba ocho horas trabajando de verdad, en mitad de lo que
    estuviera haciendo.

    Se prueba el comportamiento, no que la constante valga 3600: lo que importa
    es que la actividad renueve el plazo y que el silencio lo consuma.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="ana.sesion", password="Clave2026!")
        self.client.force_login(self.user)

    def _caduca_en(self):
        """Segundos que le quedan a la sesión según la cookie."""
        return self.client.session.get_expiry_age()

    def test_la_actividad_renueva_el_plazo(self):
        """Quien está trabajando no se entera de que hay caducidad."""
        self.client.session.set_expiry(60)  # como si quedara un minuto
        self.client.session.save()

        self.client.get(reverse("dashboard"))

        # Tras la petición el plazo vuelve a estar completo.
        self.assertGreater(self._caduca_en(), 3000)

    def test_una_sesion_sin_actividad_caduca(self):
        from django.utils import timezone
        from datetime import timedelta

        sesion = self.client.session
        # Se envejece la sesión: última actividad hace hora y media.
        sesion.set_expiry(timezone.now() - timedelta(minutes=30))
        sesion.save()

        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_el_plazo_es_de_una_hora_como_mucho(self):
        # Guarda contra volver a subirlo sin querer: el escenario que preocupa
        # —el puesto compartido con la aplicación abierta— sigue vivo todo el
        # rato que dure este número.
        self.client.get(reverse("dashboard"))
        self.assertLessEqual(self._caduca_en(), 3600)

    def test_el_perfil_de_produccion_lo_configura_asi(self):
        """Los tests de arriba usan `override_settings`, así que prueban el
        mecanismo pero no lo que realmente se despliega. Esto sí lee
        `production.py`: sin ello, alguien podría subir el plazo a 8 horas y la
        suite seguiría en verde.
        """
        import importlib

        prod = importlib.import_module("config.settings.production")
        self.assertLessEqual(prod.SESSION_COOKIE_AGE, 3600)
        self.assertTrue(
            prod.SESSION_SAVE_EVERY_REQUEST,
            "Sin esto el plazo cuenta desde el login y echa a quien está trabajando.",
        )
        self.assertTrue(prod.SESSION_EXPIRE_AT_BROWSER_CLOSE)
