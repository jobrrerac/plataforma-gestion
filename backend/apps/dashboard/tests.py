from datetime import date

from django.contrib.auth.models import Group, User
from django.test import TestCase

from apps.accounts import roles
from apps.assignments.models import Asignacion
from apps.assignments.services import disponibilidad_recursos
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


class BusquedaPorNombreTests(TestCase):
    """El buscador de solicitudes filtra por nombre de recurso además de skills."""

    @classmethod
    def setUpTestData(cls):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)
        cls.murcia = Recurso.objects.create(
            nombre="Murcia-Sanchez Juan-Daniel", email="murcia@test.com", banda="JR",
        )
        cls.franco = Recurso.objects.create(
            nombre="Franco-Campos William-Enrique", email="franco@test.com", banda="JR",
        )

    def test_servicio_filtra_por_nombre_parcial_case_insensitive(self):
        res = disponibilidad_recursos(date(2025, 1, 13), date(2025, 1, 17), None, "murcia")
        nombres = [r["recurso"].nombre for r in res]
        self.assertEqual(nombres, [self.murcia.nombre])

    def test_servicio_sin_nombre_trae_todos(self):
        res = disponibilidad_recursos(date(2025, 1, 13), date(2025, 1, 17))
        self.assertEqual(len(res), 2)

    def test_vista_solicitud_filtra_por_nombre(self):
        pm = User.objects.create_user("pm_buscador", password="pass")
        pm.groups.add(Group.objects.get(name=roles.PM))
        self.client.force_login(pm)
        resp = self.client.get("/solicitud/", {
            "fecha_inicio": "2025-01-13", "fecha_fin": "2025-01-17", "nombre": "murcia",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Murcia-Sanchez")
        self.assertNotContains(resp, "Franco-Campos")


class DiasFlexiblesTests(TestCase):
    """Sin 'Días flexibles' el PM no puede seleccionar recursos que no caben
    tal cual en lo pedido; con el flag activo el sistema ajusta."""

    @classmethod
    def setUpTestData(cls):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)
        cls.pm_user = User.objects.create_user("pm_flex", password="pass")
        cls.pm_user.groups.add(Group.objects.get(name=roles.PM))
        cls.libre = Recurso.objects.create(nombre="DevLibre", email="devlibre@test.com", banda="JR")
        cls.ocupado = Recurso.objects.create(nombre="DevOcupado", email="devocupado@test.com", banda="JR")
        cls.proyecto = Proyecto.objects.create(
            codigo="P-FLEX", nombre="Flex", cliente="X",
            fecha_inicio=date(2025, 1, 1), pm=cls.pm_user,
        )
        # El lunes 13 ene 2025 'ocupado' está a jornada completa
        Asignacion.objects.create(
            recurso=cls.ocupado, proyecto=cls.proyecto,
            fecha_inicio=date(2025, 1, 13), fecha_fin=date(2025, 1, 13),
            horas_totales=9, intensidad_diaria=8.0, jornada_completa=True,
            estado="APROBADA", solicitada_por=cls.pm_user,
        )

    def setUp(self):
        self.client.force_login(self.pm_user)

    def _resultado(self, resp, recurso):
        return next(r for r in resp.context["resultados"] if r["recurso"].pk == recurso.pk)

    def test_rango_sin_flexibles_bloquea_recurso_con_dia_lleno(self):
        resp = self.client.get("/solicitud/", {
            "fecha_inicio": "2025-01-13", "fecha_fin": "2025-01-17",
        })
        self.assertFalse(self._resultado(resp, self.ocupado)["elegible"])
        self.assertTrue(self._resultado(resp, self.libre)["elegible"])
        self.assertContains(resp, "No disponible")

    def test_rango_con_flexibles_permite_recurso_con_dia_lleno(self):
        resp = self.client.get("/solicitud/", {
            "fecha_inicio": "2025-01-13", "fecha_fin": "2025-01-17", "dias_flexibles": "on",
        })
        self.assertTrue(self._resultado(resp, self.ocupado)["elegible"])

    def test_horas_sin_flexibles_exige_horas_libres_suficientes(self):
        resp = self.client.get("/solicitud/", {
            "modo_busqueda": "horas", "fecha_inicio": "2025-01-13",
            "horas_totales": "50", "intensidad_busqueda": "8.5",
        })
        self.assertFalse(self._resultado(resp, self.ocupado)["elegible"])
        self.assertTrue(self._resultado(resp, self.libre)["elegible"])

    def _post_crear_rango(self, flexibles):
        datos = {
            "recurso": self.ocupado.pk,
            "fecha_inicio": "2025-01-13", "fecha_fin": "2025-01-17",
            "modo_crear": "rango",
            "proyecto": self.proyecto.pk,
            "intensidad_diaria": "4",
        }
        if flexibles:
            datos["dias_flexibles"] = "on"
        return self.client.post("/solicitud/crear/", datos)

    def test_crear_rango_sin_flexibles_no_ofrece_recomputo(self):
        antes = Asignacion.objects.count()
        resp = self._post_crear_rango(flexibles=False)
        self.assertContains(resp, "Días flexibles")
        self.assertIn("errores", resp.context)
        self.assertEqual(Asignacion.objects.count(), antes)

    def test_crear_rango_con_flexibles_ofrece_recomputo(self):
        resp = self._post_crear_rango(flexibles=True)
        self.assertTrue(resp.context.get("conflict_dates"))
        self.assertNotIn("errores", resp.context or {})

    def _post_recurrente(self, flexibles):
        datos = {
            "recurso": self.ocupado.pk,
            "proyecto": self.proyecto.pk,
            "fecha_inicio": "2025-01-13", "semanas": "2",
            "h_0": "4", "accion": "confirmar",
        }
        if flexibles:
            datos["dias_flexibles"] = "on"
        return self.client.post("/solicitud/recurrente/", datos)

    def test_recurrente_sin_flexibles_bloquea_sesiones_sin_cupo(self):
        antes = Asignacion.objects.count()
        resp = self._post_recurrente(flexibles=False)
        self.assertContains(resp, "Días flexibles")
        self.assertEqual(Asignacion.objects.count(), antes)

    def test_recurrente_con_flexibles_omite_y_crea(self):
        antes = Asignacion.objects.count()
        resp = self._post_recurrente(flexibles=True)
        self.assertContains(resp, "Serie creada")
        self.assertEqual(Asignacion.objects.count(), antes + 1)  # solo el lunes 20


class SolicitudRecurrenteViewTests(TestCase):
    """Vista de solicitud recurrente: previsualizar no escribe, confirmar crea la serie."""

    @classmethod
    def setUpTestData(cls):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)
        cls.pm_user = User.objects.create_user("pm_rec_view", password="pass")
        cls.pm_user.groups.add(Group.objects.get(name=roles.PM))
        cls.recurso = Recurso.objects.create(nombre="DevRecView", email="devrecview@test.com", banda="JR")
        cls.proyecto = Proyecto.objects.create(
            codigo="P-RECV", nombre="RecV", cliente="X",
            fecha_inicio=date(2025, 1, 1), pm=cls.pm_user,
        )

    def setUp(self):
        self.client.force_login(self.pm_user)

    def _post(self, accion, **extra):
        datos = {
            "recurso": self.recurso.pk,
            "proyecto": self.proyecto.pk,
            "fecha_inicio": "2025-01-13",
            "semanas": "2",
            "h_0": "4",
            "accion": accion,
        }
        datos.update(extra)
        return self.client.post("/solicitud/recurrente/", datos)

    def test_get_con_recurso(self):
        resp = self.client.get("/solicitud/recurrente/", {"recurso": self.recurso.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "DevRecView")

    def test_get_sin_recurso_muestra_selector(self):
        # Entrada directa desde el portal: se puede elegir el recurso en la página
        resp = self.client.get("/solicitud/recurrente/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Seleccioná un recurso")
        self.assertContains(resp, "DevRecView")

    def test_post_sin_recurso_da_error(self):
        resp = self._post("previsualizar", recurso="")
        self.assertContains(resp, "Seleccioná un recurso")
        self.assertEqual(Asignacion.objects.count(), 0)

    def test_previsualizar_no_crea(self):
        resp = self._post("previsualizar")
        self.assertContains(resp, "Se creará")
        self.assertEqual(Asignacion.objects.count(), 0)

    def test_confirmar_crea_serie(self):
        resp = self._post("confirmar")
        self.assertContains(resp, "Serie creada")
        asigs = list(Asignacion.objects.order_by("fecha_inicio"))
        self.assertEqual(len(asigs), 2)
        self.assertEqual(asigs[0].fecha_inicio, date(2025, 1, 13))
        self.assertEqual(asigs[1].fecha_inicio, date(2025, 1, 20))
        self.assertIsNotNone(asigs[0].serie)
        self.assertEqual(asigs[0].serie, asigs[1].serie)

    def test_sin_horas_da_error(self):
        resp = self._post("previsualizar", h_0="")
        self.assertContains(resp, "al menos un día")

    def test_ingeniero_recibe_403(self):
        ing = User.objects.create_user("ing_rec_view", password="pass")
        ing.groups.add(Group.objects.get(name=roles.INGENIERO))
        self.client.force_login(ing)
        resp = self.client.get("/solicitud/recurrente/", {"recurso": self.recurso.pk})
        self.assertEqual(resp.status_code, 403)


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
