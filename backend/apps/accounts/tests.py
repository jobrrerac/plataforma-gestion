from django.contrib import admin
from django.contrib.auth.models import AnonymousUser, Group, User
from django.core.management import call_command
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts import roles
from apps.accounts.models import CambioPasswordPendiente
from apps.legalizacion.models import DiaLegalizado, RegistroHoras


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


class SinPasswordNoLlegaAProduccionTests(TestCase):
    """El backend de desarrollo no puede existir fuera de local.

    Entrar sin contraseña es lo más peligroso que se le puede añadir a Django:
    si ese backend llegara a producción, cualquiera entraría como cualquiera
    escribiendo un nombre de usuario.

    Por eso hay tres cerrojos y este test vigila el que importa: que el módulo
    no aparezca en el perfil de producción. Los otros dos —`DEBUG` y
    `LOGIN_SIN_PASSWORD`— se comprueban abajo.
    """

    def test_produccion_no_lo_incluye(self):
        import importlib

        prod = importlib.import_module("config.settings.production")
        backends = " ".join(prod.AUTHENTICATION_BACKENDS)
        self.assertNotIn("backends_dev", backends)
        self.assertNotIn("SinPassword", backends)

    def test_produccion_no_define_la_bandera(self):
        import importlib

        prod = importlib.import_module("config.settings.production")
        self.assertFalse(getattr(prod, "LOGIN_SIN_PASSWORD", False))

    def test_base_no_lo_incluye(self):
        """`base.py` lo comparten los dos perfiles: ahí no puede estar."""
        import importlib

        base = importlib.import_module("config.settings.base")
        self.assertNotIn("backends_dev", " ".join(base.AUTHENTICATION_BACKENDS))

    @override_settings(DEBUG=False, LOGIN_SIN_PASSWORD=True)
    def test_sin_debug_no_autentica(self):
        from apps.accounts.backends_dev import LoginSinPasswordDevBackend

        User.objects.create_user(username="dev.uno", password="x")
        self.assertIsNone(
            LoginSinPasswordDevBackend().authenticate(None, username="dev.uno")
        )

    @override_settings(DEBUG=True, LOGIN_SIN_PASSWORD=False)
    def test_sin_la_bandera_no_autentica(self):
        from apps.accounts.backends_dev import LoginSinPasswordDevBackend

        User.objects.create_user(username="dev.dos", password="x")
        self.assertIsNone(
            LoginSinPasswordDevBackend().authenticate(None, username="dev.dos")
        )

    @override_settings(DEBUG=True, LOGIN_SIN_PASSWORD=True)
    def test_con_los_dos_cerrojos_si_autentica(self):
        from apps.accounts.backends_dev import LoginSinPasswordDevBackend

        u = User.objects.create_user(username="dev.tres", password="x")
        self.assertEqual(
            LoginSinPasswordDevBackend().authenticate(None, username="dev.tres"), u
        )

    @override_settings(DEBUG=True, LOGIN_SIN_PASSWORD=True)
    def test_una_cuenta_inactiva_sigue_fuera(self):
        from apps.accounts.backends_dev import LoginSinPasswordDevBackend

        User.objects.create_user(username="dev.baja", password="x", is_active=False)
        self.assertIsNone(
            LoginSinPasswordDevBackend().authenticate(None, username="dev.baja")
        )

    # Durante los tests la bandera esta forzada a False, asi que
    # AUTHENTICATION_BACKENDS no trae el backend de desarrollo. Estos dos pasan
    # por `authenticate()`, asi que hay que declararlo aqui.
    BACKENDS_DEV = [
        "apps.accounts.backends_dev.LoginSinPasswordDevBackend",
        "django.contrib.auth.backends.ModelBackend",
    ]

    @override_settings(DEBUG=True, LOGIN_SIN_PASSWORD=True, AUTHENTICATION_BACKENDS=BACKENDS_DEV)
    def test_el_formulario_de_produccion_no_se_usa_en_dev(self):
        """El campo opcional no basta: hace falta el formulario.

        `AuthenticationForm.clean()` solo llama a `authenticate()` cuando la
        contraseña tiene contenido. Con el campo vacío se saltaba esa rama, el
        formulario quedaba válido con `user_cache = None`, y el fallo aparecía
        después al iniciar sesión con None.
        """
        from apps.accounts.backends_dev import LoginSinPasswordForm

        User.objects.create_user(username="dev.form", password="x")
        formulario = LoginSinPasswordForm(data={"username": "dev.form", "password": ""})
        self.assertTrue(formulario.is_valid(), formulario.errors)
        self.assertIsNotNone(formulario.get_user())

    @override_settings(DEBUG=True, LOGIN_SIN_PASSWORD=True, AUTHENTICATION_BACKENDS=BACKENDS_DEV)
    def test_el_formulario_rechaza_a_quien_no_existe(self):
        from apps.accounts.backends_dev import LoginSinPasswordForm

        formulario = LoginSinPasswordForm(data={"username": "fantasma", "password": ""})
        self.assertFalse(formulario.is_valid())

    def test_en_produccion_la_contrasena_sigue_siendo_obligatoria(self):
        from django.contrib.auth.forms import AuthenticationForm

        User.objects.create_user(username="prod.form", password="Clave2026!")
        formulario = AuthenticationForm(data={"username": "prod.form", "password": ""})
        self.assertFalse(formulario.is_valid())


class ElAdminVeTodoLoQueHayEnElAdminTests(TestCase):
    """Cada pantalla del admin tiene que estar en los permisos del grupo Admin.

    Se descubrió porque `qa.admin@inetum.com` veía menos secciones que la cuenta
    de arranque. No era un fallo de esa cuenta: la de arranque es superusuario y
    **se salta los permisos**, así que era la única que veía el admin completo.
    Cualquier Admin de verdad entraba y le faltaban cinco pantallas — entre
    ellas el listado de firmas forzadas, construido precisamente para que
    alguien lo leyera.

    El fallo es de los que no avisan: registrar un `ModelAdmin` no da permisos a
    nadie, y quien lo prueba suele hacerlo con el superusuario. Esta prueba es
    la que convierte ese silencio en un fallo rojo.

    No exige `add`/`change`/`delete`: hay pantallas de solo lectura a propósito.
    Exige `view`, que es lo que decide si la sección aparece en el menú.
    """

    def setUp(self):
        call_command("setup_grupos", verbosity=0)

    def _permisos_del_grupo(self, nombre):
        grupo = Group.objects.get(name=nombre)
        return {
            (p.content_type.app_label, p.content_type.model)
            for p in grupo.permissions.select_related("content_type")
            if p.codename.startswith("view_")
        }

    def _modelos_del_admin(self):
        # `admin`, `sessions` y `contenttypes` son de Django y no se gestionan
        # desde este proyecto.
        return {
            (m._meta.app_label, m._meta.model_name)
            for m in admin.site._registry
            if m._meta.app_label not in ("admin", "sessions", "contenttypes")
        }

    def test_ninguna_pantalla_del_admin_se_queda_fuera(self):
        faltan = self._modelos_del_admin() - self._permisos_del_grupo("Admin")
        self.assertEqual(
            faltan, set(),
            "Estas pantallas estan registradas en el admin pero el grupo Admin "
            "no puede verlas, asi que solo las ve un superusuario: "
            f"{sorted(faltan)}. Anadirlas en setup_grupos.py.",
        )

    def test_las_cinco_que_faltaban_estan(self):
        """Escritas por su nombre: si una refactorizacion las vuelve a dejar
        fuera, se sabe cual y no hay que deducirlo del conjunto vacio."""
        permisos = self._permisos_del_grupo("Admin")
        for clave in [
            ("accounts", "cambiopasswordpendiente"),
            ("assignments", "cesionhoras"),
            ("assignments", "liberacionrecurso"),
            ("legalizacion", "dialegalizado"),
            ("legalizacion", "registrohoras"),
        ]:
            self.assertIn(clave, permisos)

    def test_un_admin_que_no_es_superusuario_ve_el_menu_completo(self):
        """La comprobacion de verdad: lo que se pinta en /admin/.

        Los permisos podrian estar bien y el menu seguir incompleto si alguna
        pantalla se registrase en otro `AdminSite`.
        """
        admin_user = User.objects.create_user("admin_no_super", "ans@test.com", "clave-larga-1")
        admin_user.is_staff = True
        admin_user.save(update_fields=["is_staff"])
        admin_user.groups.add(Group.objects.get(name="Admin"))

        self.client.force_login(admin_user)
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 200)

        visibles = {
            (app["app_label"], modelo["object_name"].lower())
            for app in resp.context["app_list"]
            for modelo in app["models"]
        }
        faltan = self._modelos_del_admin() - visibles
        self.assertEqual(
            faltan, set(),
            f"No aparecen en el menu de un Admin no superusuario: {sorted(faltan)}",
        )

    def test_el_visor_no_gana_pantallas_por_esto(self):
        """El Visor no es staff y no entra al admin; sus permisos existen solo
        para que la API en lectura le responda. Que el Admin gane pantallas no
        puede arrastrarlo a el."""
        permisos = self._permisos_del_grupo("Visor")
        self.assertNotIn(("accounts", "cambiopasswordpendiente"), permisos)
        self.assertNotIn(("legalizacion", "dialegalizado"), permisos)

    def test_el_ingeniero_sigue_sin_ver_costos(self):
        """Regla no negociable: aqui se comprueba que ampliar el Admin no le
        haya dado tarifas al Ingeniero de rebote."""
        permisos = self._permisos_del_grupo("Ingeniero")
        self.assertNotIn(("core", "tarifavigente"), permisos)
        self.assertNotIn(("assignments", "logauditoria"), permisos)


class LasHorasNoSeCorrigenDesdeElAdminTests(TestCase):
    """Corregir horas firmadas desde el admin no dejaba rastro de nada.

    `RegistroHoras` sale en dos sitios del admin: la pantalla suelta, que
    siempre fue de solo lectura, y un inline dentro del dia legalizado que **si
    era editable**. Se le dieron permisos de escritura al grupo Admin
    precisamente para poder usarlo, y era la unica forma de arreglar unas horas
    mal imputadas.

    El problema es lo que costaba: cambiar ahi las horas de un dia aprobado no
    dejaba autor, ni motivo, ni copia de lo que decia antes, y borraba de paso
    la firma del PM. Un dia legalizado es una declaracion de en que se fue una
    jornada, y alguien la reescribia sin que quedara constancia. Editar `estado`
    en el formulario del dia era ademas una reapertura silenciosa.

    Ahora se reabre el dia (`services.reabrir_dia`), que exige motivo, guarda
    quien lo hizo y conserva las firmas deshechas.

    Esto se sostiene sobre dos cosas a la vez, y ninguna sobra: los permisos del
    grupo y la clase del admin. El permiso no frena a un superusuario, y la
    clase sola dejaria concedido un permiso que no habilita nada, listo para
    reabrir el agujero sin que nadie lo note.
    """

    def setUp(self):
        call_command("setup_grupos", verbosity=0)
        self.admin = User.objects.create_user("adm_corrige", "ac@test.com", "clave-larga-1")
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        self.admin.groups.add(Group.objects.get(name="Admin"))

    def test_el_grupo_no_tiene_permiso_de_escribir_horas(self):
        tiene = self.admin.get_all_permissions()
        for accion in ("add", "change", "delete"):
            self.assertNotIn(f"legalizacion.{accion}_registrohoras", tiene)
            self.assertNotIn(f"legalizacion.{accion}_dialegalizado", tiene)

    def test_pero_las_sigue_viendo(self):
        """Quitar la escritura no es cerrar la pantalla: consultar un dia y sus
        renglones es justo lo que hace falta para decidir si hay que reabrirlo."""
        tiene = self.admin.get_all_permissions()
        self.assertIn("legalizacion.view_registrohoras", tiene)
        self.assertIn("legalizacion.view_dialegalizado", tiene)

    def test_el_inline_del_dia_no_deja_escribir(self):
        """La defensa que si alcanza al superusuario, que se salta los permisos."""
        from apps.legalizacion.admin import RegistroHorasInline

        opciones = RegistroHorasInline(DiaLegalizado, admin.site)
        self.assertFalse(opciones.has_add_permission(None))
        self.assertFalse(opciones.has_change_permission(None))
        self.assertFalse(opciones.has_delete_permission(None))

    def test_el_dia_legalizado_tampoco(self):
        """Bajar `estado` de APROBADO a ABIERTO aqui era reabrir sin decirlo."""
        from apps.legalizacion.admin import DiaLegalizadoAdmin

        opciones = DiaLegalizadoAdmin(DiaLegalizado, admin.site)
        self.assertFalse(opciones.has_add_permission(None))
        self.assertFalse(opciones.has_change_permission(None))
        self.assertFalse(opciones.has_delete_permission(None))
        self.assertIn("estado", opciones.get_readonly_fields(None))
        self.assertIn("total_horas", opciones.get_readonly_fields(None))

    def test_la_pantalla_suelta_sigue_siendo_de_solo_lectura(self):
        """Si esto empieza a fallar, alguien quito esa defensa y hay una segunda
        via de aprobacion sin bloqueo ni comprobacion de quien firma que."""
        from apps.legalizacion.admin import RegistroHorasAdmin

        opciones = RegistroHorasAdmin(RegistroHoras, admin.site)
        self.assertFalse(opciones.has_add_permission(None))
        self.assertFalse(opciones.has_change_permission(None))
        self.assertFalse(opciones.has_delete_permission(None))

    def test_los_parametros_si_se_cambian_desde_el_admin(self):
        """Es el ajuste que no deberia costar un despliegue."""
        tiene = self.admin.get_all_permissions()
        self.assertIn("legalizacion.change_parametroslegalizacion", tiene)
        self.assertNotIn("legalizacion.delete_parametroslegalizacion", tiene)

    def test_el_ingeniero_no_gana_nada_con_esto(self):
        ing = User.objects.create_user("ing_corrige", "ic@test.com", "clave-larga-1")
        ing.groups.add(Group.objects.get(name="Ingeniero"))
        tiene = ing.get_all_permissions()
        self.assertNotIn("legalizacion.change_registrohoras", tiene)
        self.assertNotIn("legalizacion.delete_registrohoras", tiene)

    def test_el_visor_tampoco(self):
        """El Visor mira y no escribe en ninguna parte."""
        visor = User.objects.create_user("vis_corrige", "vc@test.com", "clave-larga-1")
        visor.groups.add(Group.objects.get(name="Visor"))
        escribe = [p for p in visor.get_all_permissions()
                   if not p.split(".")[1].startswith("view_")]
        self.assertEqual(escribe, [], f"el Visor tiene permisos de escritura: {escribe}")
