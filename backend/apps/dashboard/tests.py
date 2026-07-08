from datetime import date

from django.contrib.auth.models import Group, User
from django.test import TestCase

from apps.accounts import roles
from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso, recursos_asignables


class RecursosAsignablesTests(TestCase):
    """El dashboard y el buscador solo muestran recursos asignables:
    se excluyen los vinculados a cuentas Admin, PM, staff o superusuario."""

    @classmethod
    def setUpTestData(cls):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)

    def _recurso(self, nombre, grupo=None, staff=False, superuser=False, con_usuario=True):
        usuario = None
        if con_usuario:
            usuario = User.objects.create_user(f"u_{nombre}", password="x", is_staff=staff)
            if superuser:
                usuario.is_superuser = True
                usuario.save(update_fields=["is_superuser"])
            if grupo:
                usuario.groups.add(Group.objects.get(name=grupo))
        return Recurso.objects.create(
            nombre=nombre, email=f"{nombre}@test.com", banda="JR", usuario=usuario,
        )

    def test_ingeniero_aparece(self):
        r = self._recurso("ing", grupo=roles.INGENIERO)
        self.assertIn(r, recursos_asignables())

    def test_sin_usuario_aparece(self):
        r = self._recurso("suelto", con_usuario=False)
        self.assertIn(r, recursos_asignables())

    def test_pm_no_aparece(self):
        r = self._recurso("pm", grupo=roles.PM)
        self.assertNotIn(r, recursos_asignables())

    def test_admin_no_aparece(self):
        r = self._recurso("adm", grupo=roles.ADMIN)
        self.assertNotIn(r, recursos_asignables())

    def test_staff_no_aparece(self):
        r = self._recurso("staff", grupo=roles.INGENIERO, staff=True)
        self.assertNotIn(r, recursos_asignables())

    def test_superusuario_no_aparece(self):
        r = self._recurso("root", superuser=True)
        self.assertNotIn(r, recursos_asignables())

    def test_inactivo_no_aparece(self):
        r = self._recurso("inactivo", grupo=roles.INGENIERO)
        r.activo = False
        r.save(update_fields=["activo"])
        self.assertNotIn(r, recursos_asignables())

    def test_api_ocupacion_excluye_pm(self):
        visible = self._recurso("visible", grupo=roles.INGENIERO)
        oculto = self._recurso("oculto_pm", grupo=roles.PM)
        self.client.force_login(User.objects.create_user("viewer2", password="pass"))
        resp = self.client.get("/api/dashboard/ocupacion/")
        ids = [r["id"] for r in resp.json()["recursos"]]
        self.assertIn(visible.pk, ids)
        self.assertNotIn(oculto.pk, ids)


class OcupacionAPIJornadaCompletaTests(TestCase):
    """Regresión: una asignación de jornada completa debe mostrarse al 100%
    todos los días, no al 94% lun–jue (8.0 placeholder / 8.5 de jornada)."""

    def setUp(self):
        self.user = User.objects.create_user("viewer", password="pass")
        self.client.force_login(self.user)
        self.pm = User.objects.create_user("pm_dash", password="pass")
        self.recurso = Recurso.objects.create(nombre="DevFull", email="devfull@test.com", banda="SR")
        self.proyecto = Proyecto.objects.create(
            codigo="P-DASH", nombre="Dash", cliente="X",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )
        # Semana sin feriados en Colombia: lunes 13 → viernes 17 de enero 2025
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2025, 1, 13), fecha_fin=date(2025, 1, 17),
            horas_totales=42, intensidad_diaria=8.0, jornada_completa=True,
            estado="APROBADA", solicitada_por=self.pm,
        )

    def _detalle(self):
        resp = self.client.get(
            "/api/dashboard/ocupacion/",
            {"fecha_inicio": "2025-01-13", "fecha_fin": "2025-01-17"},
        )
        self.assertEqual(resp.status_code, 200)
        recurso = next(r for r in resp.json()["recursos"] if r["id"] == self.recurso.pk)
        return {d["fecha"]: d for d in recurso["detalle_por_dia"]}

    def test_jornada_completa_es_100_pct_lunes_a_jueves(self):
        detalle = self._detalle()
        lunes = detalle["2025-01-13"]
        self.assertEqual(lunes["horas_asignadas"], 8.5)
        self.assertEqual(lunes["porcentaje"], 100)

    def test_jornada_completa_es_100_pct_viernes(self):
        detalle = self._detalle()
        viernes = detalle["2025-01-17"]
        self.assertEqual(viernes["horas_asignadas"], 8.0)
        self.assertEqual(viernes["porcentaje"], 100)

    def test_intensidad_parcial_no_cambia(self):
        # Una asignación normal de 4 h sigue calculándose por su intensidad
        recurso2 = Recurso.objects.create(nombre="DevMedio", email="devmedio@test.com", banda="JR")
        Asignacion.objects.create(
            recurso=recurso2, proyecto=self.proyecto,
            fecha_inicio=date(2025, 1, 13), fecha_fin=date(2025, 1, 17),
            horas_totales=20, intensidad_diaria=4.0, jornada_completa=False,
            estado="APROBADA", solicitada_por=self.pm,
        )
        resp = self.client.get(
            "/api/dashboard/ocupacion/",
            {"fecha_inicio": "2025-01-13", "fecha_fin": "2025-01-17"},
        )
        recurso = next(r for r in resp.json()["recursos"] if r["id"] == recurso2.pk)
        lunes = next(d for d in recurso["detalle_por_dia"] if d["fecha"] == "2025-01-13")
        self.assertEqual(lunes["horas_asignadas"], 4.0)
        self.assertEqual(lunes["porcentaje"], round(4.0 / 8.5 * 100, 1))
