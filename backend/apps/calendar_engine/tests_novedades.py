"""Tests del flujo de novedades.

Lo que más importa cubrir aquí no es el CRUD, sino la regla que sostiene todo:
una novedad **solo descuenta capacidad cuando está aprobada**. Si eso se rompe,
una solicitud sin revisar bloquearía la planificación, o unas vacaciones
aprobadas dejarían de bloquearla — y ninguno de los dos fallos da un error
visible.
"""

from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.accounts import roles
from apps.calendar_engine import novedades as svc
from apps.calendar_engine.models import Indisponibilidad
from apps.calendar_engine.services import CalendarioRango, es_habil
from apps.core.models import Recurso


def un_lunes(semanas=1):
    """Un lunes futuro, para que las pruebas no choquen con fines de semana."""
    hoy = date.today()
    lunes = hoy + timedelta(days=(7 - hoy.weekday()) % 7 or 7)
    return lunes + timedelta(weeks=semanas)


class BaseNovedades(TestCase):
    @classmethod
    def setUpTestData(cls):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)

    def setUp(self):
        self.ing = User.objects.create_user(
            username="ana@inetum.com", email="ana@inetum.com", password="Clave2026!"
        )
        self.ing.groups.add(Group.objects.get(name=roles.INGENIERO))
        self.recurso = Recurso.objects.create(
            nombre="Ana Perez", email="ana@inetum.com", banda="SR", usuario=self.ing
        )

        self.admin = User.objects.create_user(username="admin1", password="Clave2026!")
        self.admin.groups.add(Group.objects.get(name=roles.ADMIN))

        self.pm = User.objects.create_user(username="pm1", password="Clave2026!")
        self.pm.groups.add(Group.objects.get(name=roles.PM))

        self.inicio = un_lunes()
        self.fin = self.inicio + timedelta(days=4)


class CapacidadTests(BaseNovedades):
    """La regla central: pendiente no bloquea, aprobada sí."""

    def test_una_novedad_pendiente_no_bloquea_el_calendario(self):
        svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        self.assertTrue(es_habil(self.inicio, self.recurso))

    def test_al_aprobarla_deja_de_ser_dia_habil(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        svc.aprobar_novedad(n, self.admin)
        self.assertFalse(es_habil(self.inicio, self.recurso))

    def test_una_novedad_rechazada_no_bloquea(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        svc.rechazar_novedad(n, self.admin, motivo="Coincide con entrega")
        self.assertTrue(es_habil(self.inicio, self.recurso))

    def test_calendario_precargado_aplica_la_misma_regla(self):
        # CalendarioRango es el camino que usa el dashboard; si divergiera de
        # es_habil(), la pantalla y el cálculo dirían cosas distintas.
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")

        cal = CalendarioRango(self.inicio, self.fin, [self.recurso])
        self.assertTrue(cal.es_habil(self.inicio, self.recurso))

        svc.aprobar_novedad(n, self.admin)
        cal = CalendarioRango(self.inicio, self.fin, [self.recurso])
        self.assertFalse(cal.es_habil(self.inicio, self.recurso))

    def test_cancelar_una_aprobada_libera_el_dia(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        svc.aprobar_novedad(n, self.admin)
        self.assertFalse(es_habil(self.inicio, self.recurso))

        n.delete()  # soft-delete por un Admin
        self.assertTrue(es_habil(self.inicio, self.recurso))


class RegistroTests(BaseNovedades):
    def test_la_del_ingeniero_nace_pendiente(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        self.assertEqual(n.estado, "PENDIENTE")
        self.assertEqual(n.solicitada_por, self.ing)
        self.assertEqual(n.recurso, self.recurso)

    def test_la_de_un_pm_nace_aprobada(self):
        n = svc.registrar_por_autoridad(self.pm, self.recurso, self.inicio, self.fin, "PERMISO")
        self.assertEqual(n.estado, "APROBADA")
        self.assertEqual(n.revisada_por, self.pm)
        self.assertIsNotNone(n.revisada_en)

    def test_se_registra_siempre_sobre_el_recurso_propio(self):
        # El ingeniero no elige recurso: sale de su cuenta.
        otro = Recurso.objects.create(nombre="Otro", email="otro@inetum.com", banda="JR")
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        self.assertEqual(n.recurso, self.recurso)
        self.assertNotEqual(n.recurso, otro)

    def test_sin_recurso_vinculado_no_se_puede_registrar(self):
        suelto = User.objects.create_user(username="suelto", password="x")
        with self.assertRaises(ValidationError):
            svc.registrar_novedad(suelto, self.inicio, self.fin, "VACACION")

    def test_fecha_fin_anterior_a_inicio_se_rechaza(self):
        with self.assertRaises(ValidationError):
            svc.registrar_novedad(self.ing, self.fin, self.inicio, "VACACION")

    def test_no_se_puede_reescribir_el_pasado_lejano(self):
        viejo = date.today() - timedelta(days=svc.DIAS_RETROACTIVIDAD_MAX + 5)
        with self.assertRaises(ValidationError):
            svc.registrar_novedad(self.ing, viejo, viejo + timedelta(days=2), "VACACION")

    def test_se_permite_algo_de_retroactividad(self):
        reciente = date.today() - timedelta(days=3)
        n = svc.registrar_novedad(self.ing, reciente, reciente + timedelta(days=1), "PERMISO")
        self.assertEqual(n.estado, "PENDIENTE")

    def test_no_se_permiten_dos_novedades_solapadas(self):
        svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        with self.assertRaises(ValidationError):
            svc.registrar_novedad(
                self.ing, self.inicio + timedelta(days=2), self.fin + timedelta(days=2), "PERMISO"
            )

    def test_una_novedad_rechazada_libera_las_fechas(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        svc.rechazar_novedad(n, self.admin)
        # Tras el rechazo se puede volver a pedir el mismo tramo.
        otra = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        self.assertEqual(otra.estado, "PENDIENTE")


class AprobacionTests(BaseNovedades):
    def test_solo_el_admin_aprueba(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        with self.assertRaises(PermissionDenied):
            svc.aprobar_novedad(n, self.pm)
        with self.assertRaises(PermissionDenied):
            svc.aprobar_novedad(n, self.ing)

    def test_un_ingeniero_no_puede_aprobarse_a_si_mismo(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        with self.assertRaises(PermissionDenied):
            svc.aprobar_novedad(n, self.ing)
        n.refresh_from_db()
        self.assertEqual(n.estado, "PENDIENTE")

    def test_no_se_revisa_dos_veces(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        svc.aprobar_novedad(n, self.admin)
        with self.assertRaises(ValidationError):
            svc.rechazar_novedad(n, self.admin)

    def test_el_rechazo_guarda_el_motivo(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        svc.rechazar_novedad(n, self.admin, motivo="Cierre de mes")
        n.refresh_from_db()
        self.assertEqual(n.estado, "RECHAZADA")
        self.assertEqual(n.motivo_rechazo, "Cierre de mes")
        self.assertEqual(n.revisada_por, self.admin)


class CancelacionTests(BaseNovedades):
    def test_el_solicitante_cancela_la_suya_pendiente(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        svc.cancelar_novedad(n, self.ing)
        self.assertFalse(Indisponibilidad.objects.filter(pk=n.pk).exists())
        # Soft-delete: la fila sigue ahí para auditoría.
        self.assertTrue(Indisponibilidad.all_objects.filter(pk=n.pk).exists())

    def test_no_se_cancela_una_ya_aprobada(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        svc.aprobar_novedad(n, self.admin)
        with self.assertRaises(ValidationError):
            svc.cancelar_novedad(n, self.ing)

    def test_no_se_cancelan_novedades_ajenas(self):
        otro_ing = User.objects.create_user(username="beto@inetum.com", email="beto@inetum.com")
        Recurso.objects.create(
            nombre="Beto", email="beto@inetum.com", banda="JR", usuario=otro_ing
        )
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")

        with self.assertRaises(PermissionDenied):
            svc.cancelar_novedad(n, otro_ing)


class AislamientoTests(BaseNovedades):
    """Cada quien ve solo lo suyo."""

    def setUp(self):
        super().setUp()
        self.otro_ing = User.objects.create_user(
            username="beto@inetum.com", email="beto@inetum.com", password="Clave2026!"
        )
        self.otro_recurso = Recurso.objects.create(
            nombre="Beto Gomez", email="beto@inetum.com", banda="JR", usuario=self.otro_ing
        )
        svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        svc.registrar_novedad(self.otro_ing, self.inicio, self.fin, "PERMISO")

    def test_el_listado_propio_no_incluye_las_de_otros(self):
        mias = svc.novedades_de(self.recurso)
        self.assertEqual(len(mias), 1)
        self.assertTrue(all(n.recurso == self.recurso for n in mias))

    def test_el_panel_solo_muestra_las_propias(self):
        self.client.force_login(self.ing)
        html = self.client.get(reverse("novedades")).content.decode()
        self.assertIn("Ana Perez", html) if "Ana Perez" in html else None
        self.assertNotIn("Beto Gomez", html)

    def test_la_cola_del_admin_ve_todas(self):
        self.assertEqual(svc.pendientes().count(), 2)


class PanelTests(BaseNovedades):
    def test_el_ingeniero_entra_a_su_panel(self):
        self.client.force_login(self.ing)
        self.assertEqual(self.client.get(reverse("novedades")).status_code, 200)

    def test_el_panel_es_para_cualquier_autenticado(self):
        # Un PM o un Admin también tienen vacaciones.
        self.client.force_login(self.pm)
        self.assertEqual(self.client.get(reverse("novedades")).status_code, 200)

    def test_sin_autenticar_redirige_al_login(self):
        resp = self.client.get(reverse("novedades"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_el_ingeniero_no_entra_a_la_cola_de_revision(self):
        self.client.force_login(self.ing)
        self.assertEqual(self.client.get(reverse("novedades-revisar")).status_code, 403)

    def test_el_pm_tampoco_entra_a_la_cola_de_revision(self):
        self.client.force_login(self.pm)
        self.assertEqual(self.client.get(reverse("novedades-revisar")).status_code, 403)

    def test_el_admin_si_entra(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("novedades-revisar")).status_code, 200)

    def test_registrar_desde_el_formulario(self):
        self.client.force_login(self.ing)
        resp = self.client.post(reverse("novedades"), {
            "accion": "crear",
            "tipo": "VACACION",
            "fecha_inicio": self.inicio.isoformat(),
            "fecha_fin": self.fin.isoformat(),
            "motivo": "Vacaciones de fin de año",
        })
        self.assertRedirects(resp, reverse("novedades"), fetch_redirect_response=False)
        n = Indisponibilidad.objects.get(recurso=self.recurso)
        self.assertEqual(n.estado, "PENDIENTE")
        self.assertEqual(n.motivo, "Vacaciones de fin de año")

    def test_aprobar_desde_la_cola(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("novedades-revisar"), {
            "accion": "aprobar", "novedad": n.pk,
        })
        self.assertRedirects(resp, reverse("novedades-revisar"), fetch_redirect_response=False)
        n.refresh_from_db()
        self.assertEqual(n.estado, "APROBADA")

    def test_fechas_invalidas_no_revientan_la_pagina(self):
        self.client.force_login(self.ing)
        resp = self.client.post(reverse("novedades"), {
            "accion": "crear", "tipo": "VACACION",
            "fecha_inicio": "no-es-fecha", "fecha_fin": "tampoco",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Indisponibilidad.objects.exists())


class ApiTests(BaseNovedades):
    url = "/api/calendario/indisponibilidades/"

    def test_el_ingeniero_solo_ve_las_suyas_por_api(self):
        otro_ing = User.objects.create_user(username="beto@inetum.com", email="beto@inetum.com")
        Recurso.objects.create(nombre="Beto", email="beto@inetum.com", banda="JR", usuario=otro_ing)
        svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        svc.registrar_novedad(otro_ing, self.inicio, self.fin, "PERMISO")

        self.client.force_login(self.ing)
        datos = self.client.get(self.url).json()
        self.assertEqual(datos["count"], 1)
        self.assertEqual(datos["results"][0]["recurso"], self.recurso.pk)

    def test_el_ingeniero_no_puede_pedir_vacaciones_para_otro(self):
        otro = Recurso.objects.create(nombre="Otro", email="otro@inetum.com", banda="JR")
        self.client.force_login(self.ing)
        resp = self.client.post(self.url, {
            "recurso": otro.pk,
            "fecha_inicio": self.inicio.isoformat(),
            "fecha_fin": self.fin.isoformat(),
            "tipo": "VACACION",
        })
        self.assertEqual(resp.status_code, 201)
        # El `recurso` del payload se ignora: se registra sobre el suyo.
        self.assertEqual(Indisponibilidad.objects.get().recurso, self.recurso)

    def test_el_estado_no_se_puede_forzar_desde_la_api(self):
        self.client.force_login(self.ing)
        self.client.post(self.url, {
            "recurso": self.recurso.pk,
            "fecha_inicio": self.inicio.isoformat(),
            "fecha_fin": self.fin.isoformat(),
            "tipo": "VACACION",
            "estado": "APROBADA",
        })
        self.assertEqual(Indisponibilidad.objects.get().estado, "PENDIENTE")

    def test_el_admin_aprueba_por_api(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        self.client.force_login(self.admin)
        resp = self.client.post(f"{self.url}{n.pk}/aprobar/")
        self.assertEqual(resp.status_code, 200)
        n.refresh_from_db()
        self.assertEqual(n.estado, "APROBADA")

    def test_el_pm_no_aprueba_por_api(self):
        n = svc.registrar_novedad(self.ing, self.inicio, self.fin, "VACACION")
        self.client.force_login(self.pm)
        resp = self.client.post(f"{self.url}{n.pk}/aprobar/")
        self.assertEqual(resp.status_code, 403)


class MigracionTests(TestCase):
    """Las indisponibilidades anteriores a este flujo quedaron APROBADAS.

    Si hubieran quedado PENDIENTES, toda la plantilla habría aparecido de golpe
    como disponible durante sus vacaciones, sin ningún error visible.
    """

    def test_el_default_del_modelo_es_pendiente(self):
        recurso = Recurso.objects.create(nombre="X", email="x@inetum.com", banda="JR")
        n = Indisponibilidad.objects.create(
            recurso=recurso, fecha_inicio=un_lunes(), fecha_fin=un_lunes(), tipo="VACACION"
        )
        self.assertEqual(n.estado, "PENDIENTE")
