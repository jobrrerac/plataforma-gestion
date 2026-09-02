"""Migración de una vez de las novedades que vivían en un Excel.

Cargar ausencias aprobadas no es inocuo: descuentan capacidad y sacan esos días
de `/horas/`, así que una fila de más deja a alguien sin poder legalizar un día
que sí trabajó. Casi todo lo que se prueba aquí es cuándo **no** hay que crear:

- lo que ya terminó, que no cambia nada hacia adelante;
- lo que ya existe en la plataforma, aunque las fechas no coincidan exactas;
- los medios días, que este modelo no sabe representar.

Y una cosa del camino feliz que es fácil equivocar: el corte mira la **fecha de
fin**, no la de inicio. Unas vacaciones que empezaron en agosto y acaban mañana
siguen aplicando.
"""

import sys
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.calendar_engine.models import Indisponibilidad
from apps.core.models import Recurso

CABECERA = "Nombre\tCorreo\tFecha inicio\tFecha fin\tEstado\tNotas\n"


def ejecutar(filas, **extra):
    salida = StringIO()
    with patch.object(sys, "stdin", StringIO(CABECERA + filas)):
        call_command("migrar_novedades", "-", stdout=salida, stderr=salida, **extra)
    return salida.getvalue()


class MigrarNovedadesTests(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.recurso = Recurso.objects.create(
            nombre="Franco-Campos William-Enrique",
            email="william.franco@test.com", banda="SR",
        )

    def _fila(self, inicio, fin, notas="Vacaciones", estado="Aprobado",
              nombre=None, correo=None):
        return (
            f"{nombre or self.recurso.nombre}\t{correo or self.recurso.email}\t"
            f"{inicio:%d/%m/%Y}\t{fin:%d/%m/%Y}\t{estado}\t{notas}\n"
        )

    # ── camino feliz ────────────────────────────────────────────────────────

    def test_crea_una_ausencia_futura_ya_aprobada(self):
        futuro = self.hoy + timedelta(days=30)
        ejecutar(self._fila(futuro, futuro + timedelta(days=3)), confirmar=True)

        novedad = Indisponibilidad.objects.get()
        self.assertEqual(novedad.estado, "APROBADA")
        self.assertEqual(novedad.tipo, "VACACION")
        self.assertEqual(novedad.origen, "MANUAL")
        self.assertEqual(novedad.motivo, "Vacaciones")
        # Nadie la pidió por la aplicación: es lo que el modelo documenta para
        # lo cargado antes de existir el flujo.
        self.assertIsNone(novedad.solicitada_por)

    def test_una_ausencia_en_curso_sigue_aplicando(self):
        """El corte mira la fecha de fin. Las vacaciones de William empezaron en
        agosto y acaban pasado mañana: siguen contando."""
        ejecutar(
            self._fila(self.hoy - timedelta(days=15), self.hoy + timedelta(days=1)),
            confirmar=True,
        )
        self.assertEqual(Indisponibilidad.objects.count(), 1)

    def test_la_que_termina_hoy_tambien_entra(self):
        ejecutar(self._fila(self.hoy - timedelta(days=5), self.hoy), confirmar=True)
        self.assertEqual(Indisponibilidad.objects.count(), 1)

    def test_lo_que_no_es_vacaciones_es_permiso(self):
        """El modelo solo distingue dos: cumpleaños, Día de la Familia y
        revitalización son todos permisos."""
        futuro = self.hoy + timedelta(days=10)
        for notas in ("Día cumpleaños", "Dia de la Familia II", "Dia de revitalización"):
            Indisponibilidad.objects.all().delete()
            ejecutar(self._fila(futuro, futuro, notas=notas), confirmar=True)
            self.assertEqual(Indisponibilidad.objects.get().tipo, "PERMISO", notas)

    def test_el_revisor_queda_registrado_si_se_indica(self):
        admin = User.objects.create_user("adm_nov", "adm@test.com", "x")
        futuro = self.hoy + timedelta(days=10)
        ejecutar(self._fila(futuro, futuro), confirmar=True, revisor="adm_nov")

        novedad = Indisponibilidad.objects.get()
        self.assertEqual(novedad.revisada_por, admin)
        self.assertIsNotNone(novedad.revisada_en)

    # ── cuándo NO hay que crear ─────────────────────────────────────────────

    def test_lo_que_ya_termino_no_se_carga(self):
        pasado = self.hoy - timedelta(days=10)
        salida = ejecutar(self._fila(pasado, pasado + timedelta(days=2)), confirmar=True)

        self.assertEqual(Indisponibilidad.objects.count(), 0)
        self.assertIn("ya no aplica", salida)

    def test_lo_que_no_esta_aprobado_no_se_carga(self):
        futuro = self.hoy + timedelta(days=10)
        ejecutar(self._fila(futuro, futuro, estado="Pendiente"), confirmar=True)
        self.assertEqual(Indisponibilidad.objects.count(), 0)

    def test_un_medio_dia_no_se_carga_y_se_avisa(self):
        """Cargarlo como dia completo sacaria ese dia de /horas/ y esa persona
        no podria legalizar la mitad que si trabajo."""
        futuro = self.hoy + timedelta(days=10)
        salida = ejecutar(
            self._fila(futuro, futuro, notas="Medio día de asuntos personales"),
            confirmar=True,
        )
        self.assertEqual(Indisponibilidad.objects.count(), 0)
        self.assertIn("medio día", salida)
        self.assertIn("resolverlas a mano", salida)

    def test_no_duplica_lo_que_ya_existe_en_la_plataforma(self):
        """Luisa ya habia pedido la suya desde /novedades/."""
        futuro = self.hoy + timedelta(days=10)
        Indisponibilidad.objects.create(
            recurso=self.recurso, fecha_inicio=futuro, fecha_fin=futuro,
            tipo="PERMISO", estado="APROBADA",
        )
        salida = ejecutar(self._fila(futuro, futuro), confirmar=True)

        self.assertEqual(Indisponibilidad.objects.count(), 1)
        self.assertIn("ya existe", salida)

    def test_tampoco_duplica_si_las_fechas_solapan_sin_coincidir(self):
        """La misma ausencia pedida a mano rara vez tiene las fechas exactas."""
        inicio = self.hoy + timedelta(days=10)
        Indisponibilidad.objects.create(
            recurso=self.recurso, fecha_inicio=inicio + timedelta(days=1),
            fecha_fin=inicio + timedelta(days=2), tipo="VACACION", estado="APROBADA",
        )
        ejecutar(self._fila(inicio, inicio + timedelta(days=4)), confirmar=True)
        self.assertEqual(Indisponibilidad.objects.count(), 1)

    def test_una_rechazada_no_bloquea_la_carga(self):
        futuro = self.hoy + timedelta(days=10)
        Indisponibilidad.objects.create(
            recurso=self.recurso, fecha_inicio=futuro, fecha_fin=futuro,
            tipo="PERMISO", estado="RECHAZADA",
        )
        ejecutar(self._fila(futuro, futuro), confirmar=True)
        self.assertEqual(Indisponibilidad.objects.filter(estado="APROBADA").count(), 1)

    def test_repetir_la_carga_no_duplica(self):
        futuro = self.hoy + timedelta(days=10)
        fila = self._fila(futuro, futuro + timedelta(days=2))
        ejecutar(fila, confirmar=True)
        ejecutar(fila, confirmar=True)
        self.assertEqual(Indisponibilidad.objects.count(), 1)

    # ── negarse a adivinar ──────────────────────────────────────────────────

    def test_un_correo_desconocido_detiene_todo(self):
        futuro = self.hoy + timedelta(days=10)
        with self.assertRaises(CommandError):
            ejecutar(self._fila(futuro, futuro, correo="nadie@test.com"), confirmar=True)
        self.assertEqual(Indisponibilidad.objects.count(), 0)

    def test_si_el_nombre_no_es_el_del_correo_se_detiene(self):
        futuro = self.hoy + timedelta(days=10)
        with self.assertRaises(CommandError) as ctx:
            ejecutar(self._fila(futuro, futuro, nombre="Otra Persona"), confirmar=True)
        self.assertIn("Franco-Campos", str(ctx.exception))

    def test_fechas_al_reves_detienen_todo(self):
        futuro = self.hoy + timedelta(days=10)
        with self.assertRaises(CommandError):
            ejecutar(self._fila(futuro + timedelta(days=3), futuro), confirmar=True)

    # ── salvaguardas ────────────────────────────────────────────────────────

    def test_simular_no_escribe_nada(self):
        futuro = self.hoy + timedelta(days=10)
        salida = ejecutar(self._fila(futuro, futuro), simular=True)
        self.assertEqual(Indisponibilidad.objects.count(), 0)
        self.assertIn("No se tocó nada", salida)

    def test_sin_simular_ni_confirmar_no_hace_nada(self):
        futuro = self.hoy + timedelta(days=10)
        with self.assertRaises(CommandError):
            ejecutar(self._fila(futuro, futuro))
        self.assertEqual(Indisponibilidad.objects.count(), 0)
