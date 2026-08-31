"""Hallazgos 6, 7 y 8 de la revisión de seguridad."""

from datetime import date

from django.contrib.auth.models import Group, User
from django.test import TestCase

from apps.accounts import roles
from apps.calendar_engine.models import DiaNoLaborable, Indisponibilidad
from apps.core.models import Recurso


class DiaNoLaborableSoftDeleteTests(TestCase):
    """Un día no laborable no puede desaparecer de la base.

    Heredaba de `models.Model`, así que el DELETE de la API y el del admin lo
    borraban de verdad. Con él desaparecía la razón por la que ese día no
    computó en asignaciones ya aprobadas, y con ella la trazabilidad de por qué
    una ventana acabó cuando acabó.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser("root_dnl", "rd@test.com", "pass")
        self.admin.groups.add(Group.objects.get_or_create(name=roles.ADMIN)[0])
        self.client.force_login(self.admin)
        self.dia = DiaNoLaborable.objects.create(
            fecha=date(2026, 12, 24), descripcion="Nochebuena", creado_por=self.admin,
        )

    def test_borrar_por_api_no_lo_elimina_de_la_base(self):
        resp = self.client.delete(f"/api/calendario/dias-no-laborables/{self.dia.pk}/")
        self.assertEqual(resp.status_code, 204)
        # Fuera de los listados...
        self.assertFalse(DiaNoLaborable.objects.filter(pk=self.dia.pk).exists())
        # ...pero la fila sigue ahí, con su marca de borrado.
        self.assertTrue(DiaNoLaborable.all_objects.filter(pk=self.dia.pk).exists())
        self.assertIsNotNone(DiaNoLaborable.all_objects.get(pk=self.dia.pk).deleted_at)

    def test_la_fecha_se_puede_reutilizar_tras_borrar(self):
        """La unicidad tiene que ignorar lo borrado.

        Con `unique=True` a secas, un día eliminado seguiría ocupando su fecha
        para siempre y volver a darlo de alta fallaría sin explicación visible.
        """
        self.dia.delete()
        nuevo = DiaNoLaborable.objects.create(
            fecha=date(2026, 12, 24), descripcion="Nochebuena (recreado)", creado_por=self.admin,
        )
        self.assertNotEqual(nuevo.pk, self.dia.pk)

    def test_el_borrado_masivo_del_admin_tampoco_borra_de_verdad(self):
        self.client.post(
            "/admin/calendar_engine/dianolaborable/",
            {"action": "delete_selected", "_selected_action": [str(self.dia.pk)], "post": "yes"},
        )
        self.assertTrue(DiaNoLaborable.all_objects.filter(pk=self.dia.pk).exists())


class NovedadNoSeReasignaTests(TestCase):
    """Una novedad no puede cambiar de dueño ni de procedencia con un PATCH.

    `IndisponibilidadSerializer` dejaba editables `recurso`, `origen` y
    `external_id`. Al crear, el servidor impone el recurso del ingeniero
    —precisamente para que nadie pida vacaciones por otro—, pero esa
    comprobación se saltaba entera con un PATCH posterior.
    """

    def setUp(self):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)
        self.ing = User.objects.create_user("ana.nov", password="Clave2026!")
        self.ing.groups.add(Group.objects.get(name=roles.INGENIERO))
        self.recurso = Recurso.objects.create(
            nombre="Ana Nov", email="ana.nov@test.com", banda="SR", usuario=self.ing,
        )
        self.otro = Recurso.objects.create(
            nombre="Otro Dev", email="otro.nov@test.com", banda="JR",
        )
        self.novedad = Indisponibilidad.objects.create(
            recurso=self.recurso, fecha_inicio=date(2026, 9, 7), fecha_fin=date(2026, 9, 8),
            tipo="VACACIONES", estado="PENDIENTE", origen="MANUAL",
        )
        self.client.force_login(self.ing)
        self.url = f"/api/calendario/indisponibilidades/{self.novedad.pk}/"

    def test_no_se_puede_apuntar_a_otro_recurso(self):
        self.client.patch(
            self.url, {"recurso": self.otro.pk}, content_type="application/json",
        )
        self.novedad.refresh_from_db()
        self.assertEqual(self.novedad.recurso, self.recurso)

    def test_no_se_puede_falsear_la_procedencia(self):
        # Convertir una entrada manual en una de SAP rompe la conciliación con
        # el sistema de origen.
        self.client.patch(
            self.url, {"origen": "SAP", "external_id": "FALSO-1"},
            content_type="application/json",
        )
        self.novedad.refresh_from_db()
        self.assertEqual(self.novedad.origen, "MANUAL")
        self.assertNotEqual(self.novedad.external_id, "FALSO-1")


class HealthzNoFiltraDetallesTests(TestCase):
    """`/healthz/` es público: no puede contar cómo se llama la base de datos."""

    def test_sano_responde_ok(self):
        resp = self.client.get("/healthz/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"estado": "ok", "base_datos": "ok"})

    def test_degradado_no_incluye_el_detalle_de_la_excepcion(self):
        from unittest.mock import patch

        with patch("apps.accounts.views.connection.cursor", side_effect=Exception(
            "connection to server at 'psql-produccion.postgres.database.azure.com' "
            "port 5432 failed: FATAL: password authentication failed for user 'pgadmin'"
        )):
            resp = self.client.get("/healthz/")

        self.assertEqual(resp.status_code, 503)
        cuerpo = resp.content.decode()
        self.assertEqual(resp.json(), {"estado": "degradado"})
        for filtracion in ("psql-produccion", "pgadmin", "5432", "password"):
            self.assertNotIn(filtracion, cuerpo)
