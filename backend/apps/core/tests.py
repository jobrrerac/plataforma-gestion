from django.test import TestCase
from django.contrib.auth.models import User
from datetime import date
from .models import Recurso, Proyecto


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
