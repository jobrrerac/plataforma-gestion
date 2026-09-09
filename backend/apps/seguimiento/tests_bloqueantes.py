"""Un bloqueante es un objeto con dueño y con edad.

Lo que se blinda aquí: que quede constancia de **cuándo** se levantó la mano y
**a quién** le tocaba resolver, y que el cierre lo haga alguien que sepa si de
verdad se resolvió. La diferencia entre las dos fechas es el único indicador del
lado proyecto que no depende de que el proyecto registre nada, así que si el
cierre se relaja, el indicador deja de significar algo.
"""

from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.seguimiento import services as svc
from apps.seguimiento.models import HORAS_PARA_ESCALAR, Bloqueante


class BaseBloqueantes(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm_blk", "ab@test.com", "clave-larga-1")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.pm = User.objects.create_user("pm_blk", "pb@test.com", "clave-larga-1")
        self.pm.groups.add(Group.objects.get_or_create(name="PM")[0])
        self.ing = User.objects.create_user("ing_blk", "ib@test.com", "clave-larga-1")
        self.ing.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])

        self.recurso = Recurso.objects.create(
            nombre="Bloqueada Perez", email="ib@test.com", banda="JR", usuario=self.ing,
        )
        self.proyecto = Proyecto.objects.create(
            codigo="BLK-01", nombre="Proyecto con bloqueos", pm=self.pm,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
        )
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
            horas_totales=100, intensidad_diaria=8.5,
            estado="APROBADA", solicitada_por=self.pm,
        )

    def _abrir(self, **extra):
        datos = {"rol_que_resuelve": "PM", "proyecto": self.proyecto}
        datos.update(extra)
        return svc.abrir_bloqueante(
            self.recurso, self.ing, "el esquema de la tabla de demanda", **datos
        )


class ReportarUnBloqueoTests(BaseBloqueantes):

    def test_lo_reporta_quien_lo_sufre(self):
        b = self._abrir()
        self.assertTrue(b.abierto)
        self.assertEqual(b.recurso, self.recurso)
        self.assertEqual(b.creado_por, self.ing)

    def test_sin_decir_que_te_bloquea_no_se_guarda(self):
        with self.assertRaises(ValidationError):
            svc.abrir_bloqueante(self.recurso, self.ing, "   ", rol_que_resuelve="PM")

    def test_se_pregunta_por_rol_y_no_por_persona(self):
        """Pedirle a un junior recien llegado el NOMBRE de quien lo desbloquea es
        pedirle un dato que muchas veces no tiene: sabe que espera al jefe de
        proyecto, no como se llama."""
        b = svc.abrir_bloqueante(
            self.recurso, self.ing, "acceso al entorno de Fabric",
            rol_que_resuelve="ACCESOS",
        )
        self.assertEqual(b.rol_que_resuelve, "ACCESOS")
        self.assertEqual(b.bloqueador, "accesos o soporte técnico")

    def test_el_nombre_es_opcional_y_manda_cuando_esta(self):
        b = svc.abrir_bloqueante(
            self.recurso, self.ing, "el fichero de mapeo",
            rol_que_resuelve="CLIENTE", bloquea_nombre="Alvaro Ordaz",
        )
        self.assertEqual(b.bloqueador, "Alvaro Ordaz")

    def test_la_persona_se_deduce_cuando_se_puede(self):
        """Rol «jefe de proyecto» sobre un proyecto que tiene PM: ya sabemos
        quien es. Es lo que permite seguir midiendo por persona sin anadir una
        pregunta al formulario."""
        b = self._abrir()
        self.assertEqual(b.bloquea_usuario, self.pm)
        self.assertEqual(b.bloqueador, self.pm.get_full_name() or self.pm.username)

    def test_y_no_se_deduce_cuando_no_se_puede(self):
        b = svc.abrir_bloqueante(
            self.recurso, self.ing, "sin proyecto", rol_que_resuelve="PM",
        )
        self.assertIsNone(b.bloquea_usuario)
        self.assertEqual(b.bloqueador, "el jefe de proyecto")

    def test_nadie_reporta_por_otro(self):
        otro = Recurso.objects.create(nombre="Ajeno", email="aj@test.com", banda="JR")
        with self.assertRaises(PermissionDenied):
            svc.abrir_bloqueante(otro, self.ing, "algo", rol_que_resuelve="PM")

    def test_el_admin_si_puede_registrarlo_por_otro(self):
        """En la practica el bloqueo sale en una llamada. Si registrarlo
        dependiera de que la persona entre a la aplicacion, la mitad no llegaria."""
        b = svc.abrir_bloqueante(
            self.recurso, self.admin, "lo dijo en la daily", rol_que_resuelve="PM",
        )
        self.assertEqual(b.recurso, self.recurso)
        self.assertEqual(b.creado_por, self.admin, "queda quien lo registro")


class LaEdadDelBloqueoTests(BaseBloqueantes):
    """Sin la edad esto es una lista de quejas. Con ella es una metrica."""

    def test_un_bloqueo_recien_abierto_no_esta_vencido(self):
        b = self._abrir()
        self.assertFalse(b.vencido)
        self.assertLess(b.horas_abierto, 1)

    def test_pasado_el_plazo_se_marca(self):
        b = self._abrir()
        Bloqueante.objects.filter(pk=b.pk).update(
            creado_en=timezone.now() - timedelta(hours=HORAS_PARA_ESCALAR + 1)
        )
        b.refresh_from_db()
        self.assertTrue(b.vencido)

    def test_justo_antes_del_plazo_todavia_no(self):
        b = self._abrir()
        Bloqueante.objects.filter(pk=b.pk).update(
            creado_en=timezone.now() - timedelta(hours=HORAS_PARA_ESCALAR - 2)
        )
        b.refresh_from_db()
        self.assertFalse(b.vencido)

    def test_al_resolverlo_el_contador_se_para(self):
        b = self._abrir()
        Bloqueante.objects.filter(pk=b.pk).update(
            creado_en=timezone.now() - timedelta(hours=10)
        )
        b.refresh_from_db()
        svc.resolver_bloqueante(b, self.ing, "me lo pasaron por correo")
        b.refresh_from_db()

        self.assertFalse(b.abierto)
        self.assertFalse(b.vencido, "resuelto no puede seguir venciendo")
        self.assertAlmostEqual(b.horas_abierto, 10, delta=0.5)

    def test_un_resuelto_no_envejece_mas(self):
        b = self._abrir()
        svc.resolver_bloqueante(b, self.ing)
        b.refresh_from_db()
        primera = b.horas_abierto
        self.assertEqual(b.horas_abierto, primera)


class CerrarUnBloqueoTests(BaseBloqueantes):

    def test_lo_cierra_quien_lo_reporto(self):
        b = self._abrir()
        svc.resolver_bloqueante(b, self.ing)
        b.refresh_from_db()
        self.assertEqual(b.resuelto_por, self.ing)

    def test_tambien_quien_tenia_que_resolverlo(self):
        b = self._abrir()
        svc.resolver_bloqueante(b, self.pm, "ya se lo mande")
        b.refresh_from_db()
        self.assertEqual(b.resuelto_por, self.pm)

    def test_no_lo_cierra_cualquiera(self):
        """Si lo pudiera cerrar quien pasaba por ahi, el tiempo entre las dos
        fechas dejaria de medir nada."""
        ajeno = User.objects.create_user("ajeno_blk", "ax@test.com", "clave-larga-1")
        ajeno.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])
        b = self._abrir()
        with self.assertRaises(PermissionDenied):
            svc.resolver_bloqueante(b, ajeno)
        b.refresh_from_db()
        self.assertTrue(b.abierto)

    def test_no_se_cierra_dos_veces(self):
        b = self._abrir()
        svc.resolver_bloqueante(b, self.ing)
        with self.assertRaises(ValidationError):
            svc.resolver_bloqueante(b, self.ing)


class QuienVeQueTests(BaseBloqueantes):

    def test_el_ingeniero_ve_los_suyos(self):
        b = self._abrir()
        self.assertIn(b, svc.bloqueantes_visibles(self.ing))

    def test_y_no_los_de_otro(self):
        otro_ing = User.objects.create_user("otro_i", "oi@test.com", "clave-larga-1")
        otro_ing.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])
        otro_rec = Recurso.objects.create(
            nombre="Otra Persona", email="oi@test.com", banda="JR", usuario=otro_ing,
        )
        ajeno = svc.abrir_bloqueante(
            otro_rec, otro_ing, "otra cosa", bloquea_nombre="Alguien",
        )
        self.assertNotIn(ajeno, svc.bloqueantes_visibles(self.ing))

    def test_el_pm_ve_los_de_su_proyecto(self):
        b = self._abrir(proyecto=self.proyecto)
        self.assertIn(b, svc.bloqueantes_visibles(self.pm))

    def test_quien_bloquea_ve_lo_que_le_senalan(self):
        """Cuando la persona se dedujo, es su nombre el que aparece y lo ve."""
        b = self._abrir()
        self.assertIn(b, svc.bloqueantes_visibles(self.pm))

    def test_el_admin_lo_ve_todo(self):
        b = self._abrir()
        self.assertIn(b, svc.bloqueantes_visibles(self.admin))


class LaPantallaTests(BaseBloqueantes):

    def test_se_abre_para_cualquiera_que_haya_entrado(self):
        self.client.force_login(self.ing)
        resp = self.client.get(reverse("bloqueantes"))
        self.assertEqual(resp.status_code, 200)

    def test_sin_sesion_no(self):
        resp = self.client.get(reverse("bloqueantes"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_reportar_desde_la_pantalla(self):
        self.client.force_login(self.ing)
        resp = self.client.post(reverse("bloqueantes"), {
            "accion": "abrir",
            "necesito": "el esquema que menciona el funcional",
            "rol_que_resuelve": "PM",
            "proyecto": self.proyecto.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Bloqueante.objects.filter(recurso=self.recurso).count(), 1)

    def test_resolver_desde_la_pantalla(self):
        b = self._abrir()
        self.client.force_login(self.ing)
        self.client.post(reverse("bloqueantes"), {
            "accion": "resolver", "bloqueante": b.pk,
            "como_se_resolvio": "me lo pasaron",
        })
        b.refresh_from_db()
        self.assertFalse(b.abierto)

    def test_no_se_puede_cerrar_el_de_otro_por_la_pantalla(self):
        """La vista filtra por lo visible antes de tocar el servicio, asi que un
        id ajeno ni siquiera llega a la comprobacion de permisos."""
        otro_ing = User.objects.create_user("otro_p", "op@test.com", "clave-larga-1")
        otro_rec = Recurso.objects.create(
            nombre="Tercero", email="op@test.com", banda="JR", usuario=otro_ing,
        )
        ajeno = svc.abrir_bloqueante(
            otro_rec, otro_ing, "suyo", rol_que_resuelve="OTRO",
        )
        self.client.force_login(self.ing)
        self.client.post(reverse("bloqueantes"), {
            "accion": "resolver", "bloqueante": ajeno.pk,
        })
        ajeno.refresh_from_db()
        self.assertTrue(ajeno.abierto)


class ElVisorLoVeTodoSinTocarNadaTests(BaseBloqueantes):
    """Su papel es mirar. Lo que frena a cada persona es justo lo que necesita
    para entender una semana floja sin tener que preguntar."""

    def setUp(self):
        super().setUp()
        self.visor = User.objects.create_user("vis_blk", "vb@test.com", "clave-larga-1")
        self.visor.groups.add(Group.objects.get_or_create(name="Visor")[0])

    def test_ve_los_de_cualquiera(self):
        b = self._abrir()
        self.assertIn(b, svc.bloqueantes_visibles(self.visor))

    def test_no_puede_reportar_por_otro(self):
        with self.assertRaises(PermissionDenied):
            svc.abrir_bloqueante(
                self.recurso, self.visor, "algo", rol_que_resuelve="PM",
            )

    def test_no_puede_cerrar_el_de_otro(self):
        b = self._abrir()
        with self.assertRaises(PermissionDenied):
            svc.resolver_bloqueante(b, self.visor)


class FiltrosDeLaPantallaTests(BaseBloqueantes):
    """Con 27 personas, una lista sin filtros deja de leerse a la tercera semana."""

    def setUp(self):
        super().setUp()
        self.otro_rec = Recurso.objects.create(
            nombre="Otra Persona", email="op2@test.com", banda="JR",
        )
        self.mio = self._abrir()
        self.ajeno = svc.abrir_bloqueante(
            self.otro_rec, self.admin, "otra cosa", rol_que_resuelve="ACCESOS",
        )
        self.client.force_login(self.admin)

    def _pedir(self, **params):
        resp = self.client.get(reverse("bloqueantes-equipo"), params)
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_sin_filtros_salen_todos(self):
        resp = self._pedir()
        self.assertEqual(len(resp.context["abiertos"]), 2)

    def test_por_recurso(self):
        resp = self._pedir(recurso=self.recurso.pk)
        self.assertEqual([b.pk for b in resp.context["abiertos"]], [self.mio.pk])

    def test_por_proyecto(self):
        resp = self._pedir(proyecto=self.proyecto.pk)
        self.assertEqual([b.pk for b in resp.context["abiertos"]], [self.mio.pk])

    def test_por_estado_vencidos(self):
        Bloqueante.objects.filter(pk=self.mio.pk).update(
            creado_en=timezone.now() - timedelta(hours=HORAS_PARA_ESCALAR + 3)
        )
        resp = self._pedir(estado="VENCIDOS")
        self.assertEqual([b.pk for b in resp.context["abiertos"]], [self.mio.pk])

    def test_por_estado_resueltos_esconde_los_abiertos(self):
        svc.resolver_bloqueante(self.mio, self.ing)
        resp = self._pedir(estado="RESUELTOS")
        self.assertEqual(resp.context["abiertos"], [])
        self.assertEqual([b.pk for b in resp.context["resueltos"]], [self.mio.pk])

    def test_a_quien_solo_ve_los_suyos_no_se_le_ofrecen(self):
        """Tres desplegables sobre una lista de dos son ruido."""
        self.client.force_login(self.ing)
        resp = self.client.get(reverse("bloqueantes-equipo"))
        self.assertFalse(resp.context["puede_filtrar"])

    def test_y_no_los_puede_usar_por_la_url(self):
        """Si el filtro se aplicara igual, un ingeniero podria sondear quien
        tiene bloqueantes en un proyecto ajeno por prueba y error."""
        self.client.force_login(self.ing)
        resp = self.client.get(
            reverse("bloqueantes-equipo"), {"recurso": self.otro_rec.pk},
        )
        self.assertEqual(resp.context["f_recurso"], "")


class LosDosModosTests(BaseBloqueantes):
    """Reportar y revisar son dos tareas con dos publicos.

    Quien entra a decir que lleva dos dias esperando no tiene que atravesar
    antes un panel con los problemas de otras ocho personas; y quien entra a
    actuar no necesita el formulario de alta ocupando la mitad de la pantalla.
    """

    def test_registrar_no_ensena_alertas(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("bloqueantes"))
        self.assertEqual(resp.context["alertas"], [])

    def test_gestionar_si(self):
        b = self._abrir()
        Bloqueante.objects.filter(pk=b.pk).update(
            creado_en=timezone.now() - timedelta(hours=HORAS_PARA_ESCALAR + 3)
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("bloqueantes-equipo"))
        self.assertTrue(resp.context["alertas"])

    def test_en_registrar_solo_salen_los_propios(self):
        """Aunque el Admin alcance a ver los de todos: la pantalla es para
        reportar y cerrar lo tuyo."""
        otro_rec = Recurso.objects.create(
            nombre="Ajena Lista", email="al@test.com", banda="JR",
        )
        ajeno = svc.abrir_bloqueante(
            otro_rec, self.admin, "de otra persona", rol_que_resuelve="OTRO",
        )
        mio = self._abrir()

        self.client.force_login(self.ing)
        resp = self.client.get(reverse("bloqueantes"))
        pks = [b.pk for b in resp.context["abiertos"]]
        self.assertIn(mio.pk, pks)
        self.assertNotIn(ajeno.pk, pks)

    def test_reportar_desde_registrar_vuelve_a_registrar(self):
        self.client.force_login(self.ing)
        resp = self.client.post(reverse("bloqueantes"), {
            "accion": "abrir", "necesito": "algo", "rol_que_resuelve": "PM",
        })
        self.assertEqual(resp["Location"], reverse("bloqueantes"))

    def test_y_desde_gestionar_vuelve_a_gestionar(self):
        b = self._abrir()
        self.client.force_login(self.ing)
        resp = self.client.post(reverse("bloqueantes-equipo"), {
            "accion": "resolver", "bloqueante": b.pk,
        })
        self.assertEqual(resp["Location"], reverse("bloqueantes-equipo"))
