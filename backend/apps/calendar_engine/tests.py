from django.test import TestCase
from django.contrib.auth.models import User
from datetime import date
from .services import es_habil, calcular_fecha_fin
from .models import DiaNoLaborable


class EsHabilTests(TestCase):
    def test_sabado_no_habil(self):
        self.assertFalse(es_habil(date(2025, 1, 4)))  # sábado

    def test_domingo_no_habil(self):
        self.assertFalse(es_habil(date(2025, 1, 5)))  # domingo

    def test_lunes_comun_habil(self):
        self.assertTrue(es_habil(date(2025, 1, 7)))  # lunes sin feriado

    def test_anio_nuevo_no_habil(self):
        self.assertFalse(es_habil(date(2025, 1, 1)))

    def test_reyes_magos_emiliani_2025(self):
        # 6 ene 2025 es lunes → feriado en ese mismo día
        self.assertFalse(es_habil(date(2025, 1, 6)))

    def test_dia_independencia_colombia(self):
        self.assertFalse(es_habil(date(2025, 7, 20)))

    def test_dia_no_laborable_global(self):
        user = User.objects.create_user("u_cal", password="p")
        DiaNoLaborable.objects.create(fecha=date(2025, 3, 10), descripcion="Test", creado_por=user)
        self.assertFalse(es_habil(date(2025, 3, 10)))

    def test_dia_laborable_normal(self):
        self.assertTrue(es_habil(date(2025, 3, 11)))  # martes común


class CalcularFechaFinTests(TestCase):
    def test_5_dias_semana_completa(self):
        # Lunes 13 ene → 5 días hábiles → viernes 17 ene
        self.assertEqual(calcular_fecha_fin(date(2025, 1, 13), 5), date(2025, 1, 17))

    def test_cruza_fin_de_semana(self):
        # Jueves 9 ene → 4 días: jue, vie, lun, mar → 14 ene
        self.assertEqual(calcular_fecha_fin(date(2025, 1, 9), 4), date(2025, 1, 14))

    def test_1_dia(self):
        # Un solo día hábil: mismo día
        self.assertEqual(calcular_fecha_fin(date(2025, 1, 13), 1), date(2025, 1, 13))

    def test_cruza_feriado(self):
        # San José (19 mar) en 2025 cae miércoles → Ley Emiliani lo mueve al lunes 24 mar
        # Empezando lunes 17 mar, 3 días: lun 17, mar 18, mié 19 (no es feriado ese día) = 19 mar
        self.assertEqual(calcular_fecha_fin(date(2025, 3, 17), 3), date(2025, 3, 19))
        # Verificar que el feriado real (lunes 24 mar) sí se salta
        # 3 días desde mié 19: mié 19, jue 20, vie 21 = 21 mar
        self.assertEqual(calcular_fecha_fin(date(2025, 3, 19), 3), date(2025, 3, 21))
        # 2 días desde jue 20 cruzando el lunes 24 (feriado): jue 20, vie 21, mar 25 = 25 mar
        self.assertEqual(calcular_fecha_fin(date(2025, 3, 20), 3), date(2025, 3, 25))


class DiasNoHabilesAPITests(TestCase):
    """Endpoint que alimenta el pintado de feriados en los datepickers."""

    def setUp(self):
        self.user = User.objects.create_user("u_dnh", password="p")
        self.client.force_login(self.user)

    def test_incluye_feriados_y_no_laborables(self):
        DiaNoLaborable.objects.create(
            fecha=date(2025, 12, 24), descripcion="Cierre navideño", creado_por=self.user,
        )
        resp = self.client.get(
            "/api/calendario/dias-no-habiles/",
            {"desde": "2025-12-01", "hasta": "2025-12-31"},
        )
        self.assertEqual(resp.status_code, 200)
        por_fecha = {d["fecha"]: d for d in resp.json()}
        # 25 dic = feriado Colombia; 24 dic = día no laborable de la empresa
        self.assertEqual(por_fecha["2025-12-25"]["tipo"], "FERIADO")
        self.assertEqual(por_fecha["2025-12-24"]["tipo"], "NO_LABORABLE")
        self.assertEqual(por_fecha["2025-12-24"]["nombre"], "Cierre navideño")

    def test_rango_invalido(self):
        resp = self.client.get("/api/calendario/dias-no-habiles/", {"desde": "2025-01-01"})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.get(
            "/api/calendario/dias-no-habiles/",
            {"desde": "2025-01-01", "hasta": "2030-01-01"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_requiere_autenticacion(self):
        self.client.logout()
        resp = self.client.get(
            "/api/calendario/dias-no-habiles/",
            {"desde": "2025-01-01", "hasta": "2025-01-31"},
        )
        self.assertIn(resp.status_code, (401, 403))
