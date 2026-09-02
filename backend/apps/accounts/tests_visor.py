"""El rol Visor: ve la operación completa y no escribe en ninguna parte.

Un rol de solo lectura solo vale lo que valen sus cerraduras, así que casi todo
lo que hay aquí comprueba que **no** puede hacer algo. Que vea el dashboard es
fácil; lo difícil es que no exista ninguna puerta lateral.

El riesgo concreto que se vigila: durante mucho tiempo `es_admin_o_pm` respondía
a dos preguntas distintas —«¿puede ver todo?» y «¿puede operar?»— porque
coincidían en las mismas personas. El Visor las separa. Cambiar una comprobación
de lectura por una de escritura, o al revés, es el error natural aquí: en un
sentido le da permiso de escribir, en el otro le esconde media aplicación.
"""

from datetime import date

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from apps.accounts import roles
from apps.calendar_engine.models import Indisponibilidad
from apps.core.models import Proyecto, Recurso


class BaseVisor(TestCase):
    def setUp(self):
        for nombre in roles.TODOS:
            Group.objects.get_or_create(name=nombre)

        self.visor = self._usuario("visor1", roles.VISOR)
        self.pm = self._usuario("pm1", roles.PM)
        self.admin = self._usuario("admin1", roles.ADMIN)
        self.ingeniero = self._usuario("ing1", roles.INGENIERO)

        self.proyecto = Proyecto.objects.create(
            codigo="V-25188808/Q", nombre="Simulador", cliente="ANECOOP",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        self.recurso = Recurso.objects.create(
            nombre="Guzman-Mejia Daniel-Fernando", email="daniel@test.com", banda="SR",
        )

    def _usuario(self, username, grupo):
        user = User.objects.create_user(username, f"{username}@test.com", "clave-larga-123")
        user.groups.add(Group.objects.get(name=grupo))
        return user


class RolesDelVisorTests(BaseVisor):
    def test_es_visor_solo_para_quien_lo_es(self):
        self.assertTrue(roles.es_visor(self.visor))
        self.assertFalse(roles.es_visor(self.pm))
        self.assertFalse(roles.es_visor(self.ingeniero))

    def test_un_superusuario_no_es_un_visor(self):
        """Responder que si le esconderia los botones a quien mas los necesita."""
        raiz = User.objects.create_superuser("raiz", "raiz@test.com", "clave-larga-123")
        self.assertFalse(roles.es_visor(raiz))
        self.assertTrue(roles.puede_ver_todo(raiz))

    def test_ve_todo_pero_no_es_admin_ni_pm(self):
        """La separacion que hace posible el rol."""
        self.assertTrue(roles.puede_ver_todo(self.visor))
        self.assertFalse(roles.es_admin_o_pm(self.visor))
        self.assertFalse(roles.es_admin(self.visor))

    def test_ve_costos_y_datos_personales(self):
        """Se decidio asi al crear el rol: es de supervision."""
        self.assertTrue(roles.puede_ver_costos(self.visor))
        self.assertTrue(roles.puede_ver_datos_personales(self.visor))

    def test_el_ingeniero_sigue_sin_ver_costos(self):
        """La regla no negociable no se toco al abrir la puerta al Visor."""
        self.assertFalse(roles.puede_ver_costos(self.ingeniero))
        self.assertFalse(roles.puede_ver_todo(self.ingeniero))

    def test_no_es_staff(self):
        """El /admin/ es una herramienta de escritura por definicion."""
        self.assertFalse(roles.STAFF_POR_ROL[roles.VISOR])


class LoQueElVisorPuedeVerTests(BaseVisor):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.visor)

    def test_entra_al_dashboard(self):
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_entra_al_buscador_de_disponibilidad(self):
        """Es media razon de existir del rol; el buscador solo tiene GET."""
        self.assertEqual(self.client.get(reverse("solicitud")).status_code, 200)

    def test_ve_al_equipo_entero_y_no_solo_a_si_mismo(self):
        """Un Ingeniero se ve solo a el; el Visor supervisa."""
        respuesta = self.client.get(reverse("dashboard"))
        self.assertContains(respuesta, self.recurso.nombre)

    def test_ve_las_novedades_de_todos(self):
        Indisponibilidad.objects.create(
            recurso=self.recurso, fecha_inicio=date(2026, 10, 1),
            fecha_fin=date(2026, 10, 5), tipo="VACACION", estado="APROBADA",
        )
        respuesta = self.client.get("/api/calendario/indisponibilidades/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(respuesta.json().get("results", respuesta.json())), 1)

    def test_el_menu_lo_llama_Visor(self):
        respuesta = self.client.get(reverse("dashboard"))
        self.assertContains(respuesta, "Visor")


class LoQueElVisorNoPuedeHacerTests(BaseVisor):
    """Cada puerta de escritura, una por una."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.visor)

    def test_no_crea_solicitudes(self):
        self.assertEqual(self.client.get(reverse("solicitud-crear")).status_code, 403)

    def test_no_cede_horas(self):
        self.assertEqual(self.client.get(reverse("cesion-solicitar")).status_code, 403)

    def test_no_libera_recursos(self):
        self.assertEqual(self.client.get(reverse("liberacion-solicitar")).status_code, 403)

    def test_no_crea_solicitudes_recurrentes(self):
        self.assertEqual(self.client.get(reverse("solicitud-recurrente")).status_code, 403)

    def test_no_entra_a_la_cola_de_aprobacion(self):
        """Salvo que un proyecto lo nombre aprobador delegado, que es otra cosa."""
        self.assertEqual(self.client.get(reverse("horas-aprobar")).status_code, 403)

    def test_no_escribe_por_la_api(self):
        respuesta = self.client.post("/api/recursos/", {
            "nombre": "Alguien Nuevo", "email": "nuevo@test.com", "banda": "JR",
        })
        self.assertIn(respuesta.status_code, (403, 405))
        self.assertFalse(Recurso.objects.filter(email="nuevo@test.com").exists())

    def test_no_registra_novedades_por_autoridad(self):
        """La via de autoridad crea la novedad ya aprobada, para otra persona.

        Un Visor que ademas sea empleado puede pedir las suyas —eso no es operar,
        es ser trabajador— pero le quedan PENDIENTES como a cualquiera.
        """
        propio = Recurso.objects.create(
            nombre="Visor Uno", email="visor1@test.com", banda="SR", usuario=self.visor,
        )
        respuesta = self.client.post("/api/calendario/indisponibilidades/", {
            "recurso": self.recurso.pk,  # se ignora: no puede pedir por otro
            "fecha_inicio": "2026-11-02", "fecha_fin": "2026-11-03",
            "tipo": "VACACION", "motivo": "prueba",
        })
        self.assertIn(respuesta.status_code, (201, 400, 403))
        creadas = Indisponibilidad.objects.all()
        for novedad in creadas:
            self.assertEqual(novedad.recurso, propio, "pidio novedad para otra persona")
            self.assertEqual(novedad.estado, "PENDIENTE", "se auto-aprobo la novedad")


class CarmenComoVisorYDelegadaTests(BaseVisor):
    """El caso que motivo la decision.

    Carmen deja de ser PM y pasa a Visor, pero sigue aprobando las horas de su
    proyecto porque queda como **aprobadora delegada**. Es exactamente para lo
    que existe la delegacion: la designacion en el proyecto ES la autorizacion,
    y no depende del rol.
    """

    def test_una_visora_delegada_si_entra_a_aprobar(self):
        self.proyecto.pm = self.admin
        self.proyecto.aprobador_delegado = self.visor
        self.proyecto.save(update_fields=["pm", "aprobador_delegado"])

        self.client.force_login(self.visor)
        self.assertEqual(self.client.get(reverse("horas-aprobar")).status_code, 200)

    def test_pero_sigue_sin_poder_crear_solicitudes(self):
        """Ser delegada no la convierte en PM: solo la deja firmar ese proyecto."""
        self.proyecto.aprobador_delegado = self.visor
        self.proyecto.save(update_fields=["aprobador_delegado"])

        self.client.force_login(self.visor)
        self.assertEqual(self.client.get(reverse("solicitud-crear")).status_code, 403)


class SincronizacionDesdeEntraTests(TestCase):
    def test_visor_es_un_rol_que_llega_del_token(self):
        """Sin esto, el app role de Entra se ignora y la persona entra sin grupo."""
        from apps.accounts.oidc import GRUPOS_GESTIONADOS

        self.assertIn(roles.VISOR, GRUPOS_GESTIONADOS)
