import tempfile
from datetime import date
from pathlib import Path

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import Recurso, Proyecto
from .validators import avisos_formato_sap


class RecursoSoftDeleteTests(TestCase):
    def test_soft_delete_oculta_del_manager_default(self):
        r = Recurso.objects.create(nombre="Juan", email="juan@test.com", banda="SR")
        r.delete()
        self.assertIsNotNone(r.deleted_at)
        self.assertEqual(Recurso.objects.count(), 0)
        self.assertEqual(Recurso.all_objects.count(), 1)

    def test_str_contiene_nombre(self):
        r = Recurso(nombre="María", email="maria@test.com", banda="JR")
        self.assertIn("María", str(r))

    def test_bandas_validas(self):
        for codigo, _ in Recurso.BANDA_CHOICES:
            r = Recurso(nombre="X", email=f"{codigo}@test.com", banda=codigo)
            self.assertEqual(r.banda, codigo)


class ProyectoTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm1", password="pass")

    def test_str(self):
        p = Proyecto.objects.create(
            codigo="PRJ-001", nombre="Alpha", cliente="Acme",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )
        self.assertEqual(str(p), "PRJ-001 — Alpha")

    def test_soft_delete(self):
        p = Proyecto.objects.create(
            codigo="PRJ-002", nombre="Beta", cliente="Corp",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )
        p.delete()
        self.assertEqual(Proyecto.objects.count(), 0)
        self.assertEqual(Proyecto.all_objects.count(), 1)

    def test_grafo_opcional_y_unico(self):
        Proyecto.objects.create(
            codigo="PRJ-G1", grafo="4000111", nombre="G1", cliente="Acme",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )
        # Sin grafo: válido (nullable), varios proyectos pueden no tenerlo
        Proyecto.objects.create(
            codigo="PRJ-G2", nombre="G2", cliente="Acme",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )
        Proyecto.objects.create(
            codigo="PRJ-G3", nombre="G3", cliente="Acme",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )
        self.assertEqual(Proyecto.objects.filter(grafo__isnull=True).count(), 2)


class CargarProyectosGrafoTests(TestCase):
    """El loader de proyectos lee y actualiza la columna grafo."""

    def setUp(self):
        self.pm = User.objects.create_user("pm.loader", password="pass")

    def _cargar(self, contenido):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8", newline=""
        ) as f:
            f.write(contenido)
            ruta = f.name
        try:
            call_command("cargar_proyectos", ruta)
        finally:
            Path(ruta).unlink(missing_ok=True)

    def test_carga_con_grafo(self):
        self._cargar(
            "codigo,codigo_pep,grafo,nombre,cliente,fecha_inicio,pm_username\n"
            "T-001,P-2026-1,4000123456,Test,Acme,2026-01-15,pm.loader\n"
        )
        p = Proyecto.objects.get(codigo="T-001")
        self.assertEqual(p.grafo, "4000123456")
        self.assertEqual(p.codigo_pep, "P-2026-1")

    def test_upsert_actualiza_grafo(self):
        self._cargar(
            "codigo,grafo,nombre,cliente,fecha_inicio,pm_username\n"
            "T-002,4000999,Test2,Acme,2026-01-15,pm.loader\n"
        )
        self._cargar(
            "codigo,grafo,nombre,cliente,fecha_inicio,pm_username\n"
            "T-002,4000888,Test2,Acme,2026-01-15,pm.loader\n"
        )
        self.assertEqual(Proyecto.objects.get(codigo="T-002").grafo, "4000888")

    def test_sin_columna_grafo_queda_nulo(self):
        self._cargar(
            "codigo,nombre,cliente,fecha_inicio,pm_username\n"
            "T-003,Test3,Acme,2026-01-15,pm.loader\n"
        )
        self.assertIsNone(Proyecto.objects.get(codigo="T-003").grafo)


class MascarasSAPTests(TestCase):
    """Máscaras de formato SAP: opcionales por defecto, bloqueantes en modo estricto."""

    def setUp(self):
        self.pm = User.objects.create_user("pm.sap", password="pass")

    def _proyecto(self, **kwargs):
        defaults = dict(
            codigo="V-00869252/D", nombre="SAP", cliente="Acme",
            fecha_inicio=date(2026, 1, 1), pm=self.pm,
        )
        defaults.update(kwargs)
        return Proyecto(**defaults)

    def test_formatos_correctos_pasan_en_estricto(self):
        with override_settings(SAP_VALIDACION_ESTRICTA=True):
            p = self._proyecto(codigo_pep="L-00869252/A", grafo="2000269630")
            p.full_clean()  # no debe lanzar

    def test_modo_no_estricto_acepta_legados(self):
        # Default (False): QA-001 y PEP/grafo con otro formato no bloquean
        p = self._proyecto(codigo="QA-001", codigo_pep="P-2026-00123", grafo="ABC")
        p.full_clean()  # no debe lanzar

    def test_estricto_rechaza_codigo_invalido(self):
        with override_settings(SAP_VALIDACION_ESTRICTA=True):
            with self.assertRaises(ValidationError):
                self._proyecto(codigo="QA-001").full_clean()

    def test_estricto_rechaza_pep_invalido(self):
        with override_settings(SAP_VALIDACION_ESTRICTA=True):
            with self.assertRaises(ValidationError):
                self._proyecto(codigo_pep="L-123/A").full_clean()

    def test_estricto_rechaza_grafo_invalido(self):
        with override_settings(SAP_VALIDACION_ESTRICTA=True):
            with self.assertRaises(ValidationError):
                self._proyecto(grafo="123").full_clean()

    def test_estricto_permite_pep_y_grafo_vacios(self):
        # "No obligatoria": la máscara valida formato, no exige el dato
        with override_settings(SAP_VALIDACION_ESTRICTA=True):
            self._proyecto().full_clean()  # sin PEP ni grafo

    def test_avisos_formato(self):
        avisos = avisos_formato_sap(codigo="QA-001", codigo_pep="L-00869252/A", grafo="20x")
        self.assertEqual(len(avisos), 2)  # codigo y grafo mal, PEP bien
        self.assertEqual(avisos_formato_sap(codigo="V-00869252/D"), [])
