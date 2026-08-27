"""Tests de la aprobación de horas.

Lo que hay que blindar: quién puede aprobar qué, que el Admin sirva de válvula
de escape cuando el PM no está, y que devolver un día lo reabra dejando dicho
qué corregir.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts import roles
from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.legalizacion import services as svc
from apps.legalizacion.models import DiaLegalizado, TipoActividad


def ultimo_miercoles():
    hoy = date.today()
    dias_atras = (hoy.weekday() - 2) % 7 or 7
    return hoy - timedelta(days=dias_atras)


class BaseAprobacion(TestCase):
    @classmethod
    def setUpTestData(cls):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)
        call_command("setup_actividades", verbosity=0)

    def setUp(self):
        self.ing = User.objects.create_user(
            username="ana@inetum.com", email="ana@inetum.com", password="Clave2026!"
        )
        self.ing.groups.add(Group.objects.get(name=roles.INGENIERO))
        self.recurso = Recurso.objects.create(
            nombre="Ana Perez", email="ana@inetum.com", banda="SR", usuario=self.ing
        )

        self.pm = User.objects.create_user(username="pm.suyo", password="Clave2026!")
        self.pm.groups.add(Group.objects.get(name=roles.PM))
        self.otro_pm = User.objects.create_user(username="pm.ajeno", password="Clave2026!")
        self.otro_pm.groups.add(Group.objects.get(name=roles.PM))

        self.admin = User.objects.create_user(username="admin1", password="Clave2026!")
        self.admin.groups.add(Group.objects.get(name=roles.ADMIN))

        self.proyecto = Proyecto.objects.create(
            codigo="V-22222222/B", nombre="Proyecto Cliente", cliente="ACME",
            fecha_inicio=date(2026, 1, 1), pm=self.pm, facturable=True,
        )
        self.t_proyecto = TipoActividad.objects.get(nombre="Proyecto")
        self.t_estudio = TipoActividad.objects.get(nombre="Estudio")

        self.fecha = ultimo_miercoles()
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=self.fecha, fecha_fin=self.fecha,
            horas_totales=34, intensidad_diaria=8.5,
            estado="APROBADA", solicitada_por=self.pm,
        )

    def _dia_registrado(self, con_proyecto=True):
        """Un día cerrado y listo para revisar."""
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        if con_proyecto:
            svc.agregar_renglon(dia, self.t_proyecto, 8.5, "Desarrollo", proyecto=self.proyecto)
        else:
            svc.agregar_renglon(dia, self.t_estudio, 8.5, "Curso de Django")
        return svc.registrar_dia(dia, self.ing)


class AlcanceDeLaColaTests(BaseAprobacion):
    def test_el_pm_ve_los_dias_de_sus_proyectos(self):
        dia = self._dia_registrado()
        self.assertIn(dia, svc.dias_por_aprobar(self.pm))

    def test_un_pm_ajeno_no_ve_esos_dias(self):
        self._dia_registrado()
        self.assertEqual(svc.dias_por_aprobar(self.otro_pm).count(), 0)

    def test_el_admin_lo_ve_todo(self):
        # Es la valvula de escape: si el PM no esta, las horas no pueden
        # quedarse bloqueadas para siempre.
        dia = self._dia_registrado()
        self.assertIn(dia, svc.dias_por_aprobar(self.admin))

    def test_el_admin_ve_hasta_los_dias_de_proyectos_ajenos(self):
        self.proyecto.pm = self.otro_pm
        self.proyecto.save(update_fields=["pm"])
        dia = self._dia_registrado()
        self.assertIn(dia, svc.dias_por_aprobar(self.admin))

    def test_un_dia_sin_proyecto_solo_lo_ve_el_admin(self):
        # Un dia de puro estudio no tiene PM que lo reclame. Sin el alcance
        # total del Admin, no se aprobaria nunca.
        dia = self._dia_registrado(con_proyecto=False)
        self.assertEqual(svc.dias_por_aprobar(self.pm).count(), 0)
        self.assertIn(dia, svc.dias_por_aprobar(self.admin))

    def test_un_ingeniero_no_aprueba_nada(self):
        self._dia_registrado()
        self.assertEqual(svc.dias_por_aprobar(self.ing).count(), 0)

    def test_un_dia_abierto_no_esta_en_la_cola(self):
        # Todavia no lo ha aceptado quien lo registra.
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        svc.agregar_renglon(dia, self.t_proyecto, 4, "A medias", proyecto=self.proyecto)
        self.assertNotIn(dia, svc.dias_por_aprobar(self.admin))


class AprobarTests(BaseAprobacion):
    def test_el_pm_aprueba_y_queda_su_firma(self):
        dia = self._dia_registrado()
        svc.aprobar_dia(dia, self.pm)

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.APROBADO)
        self.assertEqual(dia.aprobado_por, self.pm)
        self.assertIsNotNone(dia.aprobado_en)

    def test_el_admin_puede_aprobar_en_lugar_del_pm(self):
        dia = self._dia_registrado()
        svc.aprobar_dia(dia, self.admin)

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.APROBADO)
        self.assertEqual(dia.aprobado_por, self.admin)

    def test_un_pm_ajeno_no_puede_aprobar(self):
        dia = self._dia_registrado()
        with self.assertRaises(PermissionDenied):
            svc.aprobar_dia(dia, self.otro_pm)

    def test_el_ingeniero_no_se_aprueba_a_si_mismo(self):
        dia = self._dia_registrado()
        with self.assertRaises(PermissionDenied):
            svc.aprobar_dia(dia, self.ing)

    def test_no_se_aprueba_dos_veces(self):
        dia = self._dia_registrado()
        svc.aprobar_dia(dia, self.pm)
        with self.assertRaises(ValidationError):
            svc.aprobar_dia(dia, self.admin)

    def test_no_se_aprueba_un_dia_todavia_abierto(self):
        # El error tiene que hablar del estado, no de permisos: el Admin sí
        # puede revisarlo, lo que pasa es que aún no está cerrado.
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        svc.agregar_renglon(dia, self.t_proyecto, 4, "A medias", proyecto=self.proyecto)
        with self.assertRaises(ValidationError) as ctx:
            svc.aprobar_dia(dia, self.admin)
        self.assertIn("todavía no lo ha aceptado", "; ".join(ctx.exception.messages))

    def test_el_error_de_aprobar_dos_veces_dice_la_verdad(self):
        # Regresión: el chequeo de permiso miraba solo días REGISTRADOS, así
        # que un día ya aprobado parecía fuera de alcance y el PM legítimo leía
        # "no eres PM de ninguno de sus proyectos" — falso y desorientador.
        dia = self._dia_registrado()
        svc.aprobar_dia(dia, self.pm)

        with self.assertRaises(ValidationError) as ctx:
            svc.aprobar_dia(dia, self.pm)
        self.assertIn("ya está aprobado", "; ".join(ctx.exception.messages))


class DevolverTests(BaseAprobacion):
    def test_devolver_reabre_el_dia(self):
        dia = self._dia_registrado()
        svc.devolver_dia(dia, self.pm, "Faltan las horas de la reunion")

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.ABIERTO)
        self.assertTrue(dia.editable)
        self.assertIsNone(dia.registrado_en)

    def test_el_motivo_es_obligatorio(self):
        # Devolver sin decir que esta mal solo produce un segundo intento a
        # ciegas.
        dia = self._dia_registrado()
        with self.assertRaises(ValidationError):
            svc.devolver_dia(dia, self.pm, "   ")

    def test_el_motivo_queda_guardado_para_quien_lo_registro(self):
        dia = self._dia_registrado()
        svc.devolver_dia(dia, self.pm, "Faltan las horas de la reunion")

        dia.refresh_from_db()
        self.assertEqual(dia.motivo_devolucion, "Faltan las horas de la reunion")

    def test_tras_devolverlo_se_puede_volver_a_editar(self):
        dia = self._dia_registrado()
        svc.devolver_dia(dia, self.pm, "Corrige el detalle")

        dia.refresh_from_db()
        svc.guardar_renglones(dia, [
            {"tipo_actividad": self.t_proyecto, "proyecto": self.proyecto,
             "horas": "8.5", "detalle": "Desarrollo, ahora bien explicado"},
        ])
        self.assertEqual(dia.registros.count(), 1)

    def test_al_aprobar_se_limpia_un_motivo_anterior(self):
        # Si no, el dia quedaria aprobado y con un reproche pegado.
        dia = self._dia_registrado()
        svc.devolver_dia(dia, self.pm, "Corrige el detalle")
        dia.refresh_from_db()

        svc.registrar_dia(dia, self.ing)
        dia.refresh_from_db()
        svc.aprobar_dia(dia, self.pm)

        dia.refresh_from_db()
        self.assertEqual(dia.motivo_devolucion, "")

    def test_un_pm_ajeno_no_puede_devolver(self):
        dia = self._dia_registrado()
        with self.assertRaises(PermissionDenied):
            svc.devolver_dia(dia, self.otro_pm, "No me gusta")


class PantallaAprobacionTests(BaseAprobacion):
    def test_el_pm_entra(self):
        self.client.force_login(self.pm)
        self.assertEqual(self.client.get(reverse("horas-aprobar")).status_code, 200)

    def test_el_admin_entra(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("horas-aprobar")).status_code, 200)

    def test_el_ingeniero_recibe_403(self):
        self.client.force_login(self.ing)
        self.assertEqual(self.client.get(reverse("horas-aprobar")).status_code, 403)

    def test_al_admin_se_le_avisa_de_su_alcance(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("horas-aprobar")).content.decode()
        self.assertIn("Como administrador ves", html)

    def test_al_pm_no_se_le_muestra_esa_nota(self):
        self.client.force_login(self.pm)
        html = self.client.get(reverse("horas-aprobar")).content.decode()
        self.assertNotIn("Como administrador ves", html)

    def test_aprobar_desde_la_pantalla(self):
        dia = self._dia_registrado()
        self.client.force_login(self.pm)
        resp = self.client.post(reverse("horas-aprobar"), {"accion": "aprobar", "dia": dia.pk})

        self.assertRedirects(resp, reverse("horas-aprobar"), fetch_redirect_response=False)
        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.APROBADO)

    def test_devolver_desde_la_pantalla(self):
        dia = self._dia_registrado()
        self.client.force_login(self.pm)
        self.client.post(reverse("horas-aprobar"), {
            "accion": "devolver", "dia": dia.pk, "motivo": "Falta detalle",
        })
        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.ABIERTO)

    def test_la_cola_muestra_el_desglose_no_solo_el_total(self):
        # Quien aprueba necesita ver a que se fueron las horas.
        self._dia_registrado()
        self.client.force_login(self.pm)
        html = self.client.get(reverse("horas-aprobar")).content.decode()
        self.assertIn("V-22222222/B", html)
        self.assertIn("Desarrollo", html)

    def test_el_ingeniero_ve_el_motivo_de_la_devolucion(self):
        dia = self._dia_registrado()
        svc.devolver_dia(dia, self.pm, "Faltan las horas de la reunion")

        self.client.force_login(self.ing)
        html = self.client.get(f"{reverse('horas')}?fecha={self.fecha.isoformat()}").content.decode()
        self.assertIn("Te devolvieron este día para corregir", html)
        self.assertIn("Faltan las horas de la reunion", html)


class FacturablesTests(BaseAprobacion):
    def test_el_resumen_separa_facturables_de_lo_que_no(self):
        interno = Proyecto.objects.create(
            codigo="INT-X", nombre="Interno", cliente="Inetum",
            fecha_inicio=date(2026, 1, 1), pm=self.admin, facturable=False,
        )
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        svc.agregar_renglon(dia, self.t_proyecto, 6, "Desarrollo", proyecto=self.proyecto)
        svc.agregar_renglon(dia, self.t_proyecto, 1.5, "Gestion", proyecto=interno)
        svc.agregar_renglon(dia, self.t_estudio, 1, "Curso")

        datos = svc.resumen(dia)
        self.assertEqual(datos["facturables"], Decimal("6"))
        self.assertEqual(datos["no_facturables"], Decimal("2.5"))
