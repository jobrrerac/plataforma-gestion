from django.test import TestCase
from django.contrib.auth.models import User
from datetime import date
from apps.core.models import Recurso, Proyecto
from .models import Asignacion, LogAuditoria
from .services import calcular_fecha_fin, puede_asignar, aprobar_asignacion


class CalculoFechaFinTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm", password="pass")
        self.recurso = Recurso.objects.create(nombre="Dev1", email="dev1@test.com", banda="SR")

    def test_40h_a_8h_dia(self):
        # 40h / 8h = 5 días, lunes 13 ene → viernes 17 ene 2025
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 40, 8)
        self.assertEqual(fecha, date(2025, 1, 17))

    def test_20h_a_4h_dia(self):
        # 20h / 4h = 5 días, lunes 13 ene → viernes 17 ene 2025
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 20, 4)
        self.assertEqual(fecha, date(2025, 1, 17))

    def test_8h_a_8h_dia(self):
        # 1 día, lunes → mismo lunes
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 8, 8)
        self.assertEqual(fecha, date(2025, 1, 13))

    def test_cruza_fin_de_semana(self):
        # 16h / 8h = 2 días, viernes 10 ene → lunes 13 ene
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 10), 16, 8)
        self.assertEqual(fecha, date(2025, 1, 13))


class CapacidadTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm2", password="pass")
        self.admin = User.objects.create_user("admin2", password="pass")
        self.recurso = Recurso.objects.create(nombre="Dev2", email="dev2@test.com", banda="SR")
        self.proyecto = Proyecto.objects.create(
            codigo="P-001", nombre="Alpha", cliente="X",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )

    def _crear_aprobada(self, horas=40, intensidad=8, inicio=date(2025, 1, 13)):
        fecha_fin = calcular_fecha_fin(self.recurso, inicio, horas, intensidad)
        a = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=horas, intensidad_diaria=intensidad,
            fecha_inicio=inicio, fecha_fin=fecha_fin,
            estado="APROBADA", solicitada_por=self.pm,
        )
        return a

    def test_sobreasignacion_bloqueada(self):
        self._crear_aprobada(horas=40, intensidad=8)
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 8, 1)
        candidata = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=8, intensidad_diaria=1,
            fecha_inicio=date(2025, 1, 13), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(candidata)
        self.assertFalse(ok)

    def test_50_porciento_mas_50_porciento_valido(self):
        self._crear_aprobada(horas=20, intensidad=4)
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 20, 4)
        candidata = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=20, intensidad_diaria=4,
            fecha_inicio=date(2025, 1, 13), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(candidata)
        self.assertTrue(ok)

    def test_log_auditoria_se_crea_al_aprobar(self):
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 40, 8)
        asig = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=40, intensidad_diaria=8,
            fecha_inicio=date(2025, 1, 13), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        aprobar_asignacion(asig, self.admin)
        self.assertEqual(asig.estado, "APROBADA")
        self.assertTrue(LogAuditoria.objects.filter(asignacion=asig, accion="APROBAR").exists())
