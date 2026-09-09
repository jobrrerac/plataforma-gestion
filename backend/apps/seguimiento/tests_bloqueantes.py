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
        datos = {"bloquea_usuario": self.pm}
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
        self.assertEqual(b.bloqueador, self.pm.get_full_name() or self.pm.username)

    def test_sin_decir_que_necesitas_no_se_guarda(self):
        with self.assertRaises(ValidationError):
            svc.abrir_bloqueante(self.recurso, self.ing, "   ", bloquea_usuario=self.pm)

    def test_sin_dueno_no_se_guarda(self):
        """Un bloqueante que no le toca a nadie no lo desatasca nadie, y de paso
        no produce ningun indicador."""
        with self.assertRaises(ValidationError):
            svc.abrir_bloqueante(self.recurso, self.ing, "algo")

    def test_vale_una_persona_de_fuera(self):
        """La mitad de quienes desbloquean son del equipo cliente y no tienen
        cuenta. Si solo se aceptara una FK, esos casos no se reportarian."""
        b = svc.abrir_bloqueante(
            self.recurso, self.ing, "acceso al Fabric",
            bloquea_nombre="Alvaro Ordaz",
        )
        self.assertEqual(b.bloqueador, "Alvaro Ordaz")

    def test_nadie_reporta_por_otro(self):
        otro = Recurso.objects.create(nombre="Ajeno", email="aj@test.com", banda="JR")
        with self.assertRaises(PermissionDenied):
            svc.abrir_bloqueante(otro, self.ing, "algo", bloquea_usuario=self.pm)

    def test_el_admin_si_puede_registrarlo_por_otro(self):
        """En la practica el bloqueo sale en una llamada. Si registrarlo
        dependiera de que la persona entre a la aplicacion, la mitad no llegaria."""
        b = svc.abrir_bloqueante(
            self.recurso, self.admin, "lo dijo en la daily", bloquea_usuario=self.pm,
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
        """Aunque no sea de un proyecto suyo: es su nombre el que aparece."""
        b = svc.abrir_bloqueante(
            self.recurso, self.ing, "sin proyecto", bloquea_usuario=self.pm,
        )
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
            "bloquea_usuario": self.pm.pk,
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
            otro_rec, otro_ing, "suyo", bloquea_nombre="X",
        )
        self.client.force_login(self.ing)
        self.client.post(reverse("bloqueantes"), {
            "accion": "resolver", "bloqueante": ajeno.pk,
        })
        ajeno.refresh_from_db()
        self.assertTrue(ajeno.abierto)
