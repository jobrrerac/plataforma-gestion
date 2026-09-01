"""Carga del N° de persona SAP desde una lista de RRHH.

El riesgo de este comando no es que falle: es que funcione y escriba mal. Un
número SAP equivocado no se ve en ninguna pantalla —no cambia nombres, ni
horas, ni costos— y aparece meses después, cuando alguien cruza la plataforma
con SAP y los datos no cuadran.

De ahí que casi todas las pruebas sean de lo que el comando tiene que **impedir**:

- que un archivo con las columnas descuadradas le ponga a cada persona el
  número de la siguiente,
- que se pise en silencio un número que ya estaba puesto y era distinto,
- que dos personas acaben con el mismo número, que la base rechazaría a media
  escritura con un mensaje que no dice de quién se trata.

Y una que es del camino feliz pero se rompe fácil: escribir SOLO ese campo. Es
el motivo de que este comando exista en vez de reutilizar `cargar_recursos`.
"""

import sys
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.core.models import Recurso


def ejecutar(archivo, **extra):
    """Corre el comando leyendo el TSV de la entrada estándar."""
    salida = StringIO()
    with patch.object(sys, "stdin", StringIO(archivo)):
        call_command("actualizar_sap", "-", stdout=salida, stderr=salida, **extra)
    return salida.getvalue()


class ActualizarSapTests(TestCase):
    def setUp(self):
        self.martin = Recurso.objects.create(
            nombre="Medina-Novoa Martin", email="martin@test.com", banda="SSR",
        )
        self.santiago = Recurso.objects.create(
            nombre="Martinez-Herrera Santiago-Orlando", email="santiago@test.com", banda="JR",
        )

    # ── camino feliz ────────────────────────────────────────────────────────

    def test_rellena_los_numeros_que_faltaban(self):
        ejecutar(
            "Medina-Novoa Martin\tmartin@test.com\t30011555\n"
            "Martinez-Herrera Santiago-Orlando\tsantiago@test.com\t30011565\n",
            confirmar=True,
        )
        self.martin.refresh_from_db()
        self.santiago.refresh_from_db()
        self.assertEqual(self.martin.nro_persona_sap, "30011555")
        self.assertEqual(self.santiago.nro_persona_sap, "30011565")

    def test_el_update_solo_escribe_la_columna_del_sap(self):
        """Es la razon de ser del comando: `cargar_recursos` reescribiria banda,
        grupos, skills y tarifas a partir de columnas que aqui no vienen.

        Se mira el SQL y no solo el resultado. Comparar los campos despues no
        prueba nada: el comando relee el recurso justo antes de guardarlo, asi
        que un `save()` completo escribiria los mismos valores y la prueba
        pasaria igual. Lo que hay que fijar es que la sentencia no los toque, que
        es lo que protege a quien edite ese recurso al mismo tiempo.
        """
        self.martin.banda = "LEAD"
        self.martin.activo = False
        self.martin.save()

        with CaptureQueriesContext(connection) as consultas:
            ejecutar("Medina-Novoa Martin\tmartin@test.com\t30011555\n", confirmar=True)

        updates = [
            c["sql"] for c in consultas.captured_queries
            if c["sql"].lstrip().upper().startswith("UPDATE")
            and "core_recurso" in c["sql"]
        ]
        self.assertEqual(len(updates), 1, f"se esperaba un solo UPDATE, hubo {len(updates)}")
        sentencia = updates[0]
        self.assertIn("nro_persona_sap", sentencia)
        for columna in ("banda", "activo", "nombre", "email", "deleted_at"):
            self.assertNotIn(columna, sentencia, f"el UPDATE no deberia tocar '{columna}'")

        self.martin.refresh_from_db()
        self.assertEqual(self.martin.nro_persona_sap, "30011555")
        self.assertEqual(self.martin.banda, "LEAD")
        self.assertFalse(self.martin.activo)

    def test_se_salta_la_cabecera(self):
        ejecutar(
            "Nombre\tCorreo\tSAP ID\n"
            "Medina-Novoa Martin\tmartin@test.com\t30011555\n",
            confirmar=True,
        )
        self.martin.refresh_from_db()
        self.assertEqual(self.martin.nro_persona_sap, "30011555")

    def test_repetirlo_no_cambia_nada(self):
        archivo = "Medina-Novoa Martin\tmartin@test.com\t30011555\n"
        ejecutar(archivo, confirmar=True)
        salida = ejecutar(archivo, confirmar=True)
        self.assertIn("ya estaba", salida)
        self.martin.refresh_from_db()
        self.assertEqual(self.martin.nro_persona_sap, "30011555")

    def test_las_tildes_y_guiones_del_nombre_no_lo_hacen_fallar(self):
        """'Peña-Ayala Juan-Camilo' y 'Pena Ayala Juan Camilo' son la misma persona."""
        Recurso.objects.create(
            nombre="Peña-Ayala Juan-Camilo", email="juan@test.com", banda="JR",
        )
        ejecutar("Pena Ayala Juan Camilo\tjuan@test.com\t30011558\n", confirmar=True)
        self.assertEqual(
            Recurso.objects.get(email="juan@test.com").nro_persona_sap, "30011558",
        )

    # ── lo que tiene que impedir ────────────────────────────────────────────

    def test_si_el_nombre_no_es_el_del_correo_se_detiene(self):
        """Columnas descuadradas: cada persona acabaria con el numero de otra."""
        with self.assertRaises(CommandError) as ctx:
            ejecutar("Medina-Novoa Martin\tsantiago@test.com\t30011555\n", confirmar=True)
        self.assertIn("Martinez-Herrera Santiago-Orlando", str(ctx.exception))
        self.santiago.refresh_from_db()
        self.assertFalse(self.santiago.nro_persona_sap)

    def test_un_numero_distinto_al_que_ya_tenia_detiene_todo(self):
        self.martin.nro_persona_sap = "30011077"
        self.martin.save()

        with self.assertRaises(CommandError) as ctx:
            ejecutar(
                "Medina-Novoa Martin\tmartin@test.com\t30011570\n"
                "Martinez-Herrera Santiago-Orlando\tsantiago@test.com\t30011565\n",
                confirmar=True,
            )
        self.assertIn("--sobrescribir", str(ctx.exception))
        # Ni siquiera se escribe el que no estaba en conflicto.
        self.martin.refresh_from_db()
        self.santiago.refresh_from_db()
        self.assertEqual(self.martin.nro_persona_sap, "30011077")
        self.assertFalse(self.santiago.nro_persona_sap)

    def test_con_sobrescribir_si_se_aplica(self):
        self.martin.nro_persona_sap = "30011077"
        self.martin.save()

        salida = ejecutar(
            "Medina-Novoa Martin\tmartin@test.com\t30011570\n",
            confirmar=True, sobrescribir=True,
        )
        self.assertIn("ya tenia 30011077", salida)
        self.martin.refresh_from_db()
        self.assertEqual(self.martin.nro_persona_sap, "30011570")

    def test_dos_personas_con_el_mismo_numero_se_detiene(self):
        """La base lo rechazaria a media escritura sin decir de quien se trata."""
        with self.assertRaises(CommandError) as ctx:
            ejecutar(
                "Medina-Novoa Martin\tmartin@test.com\t30011555\n"
                "Martinez-Herrera Santiago-Orlando\tsantiago@test.com\t30011555\n",
                confirmar=True,
            )
        self.assertIn("repite números SAP", str(ctx.exception))
        self.assertEqual(Recurso.objects.exclude(nro_persona_sap=None).count(), 0)

    def test_un_numero_que_ya_es_de_otro_recurso_se_detiene(self):
        self.santiago.nro_persona_sap = "30011555"
        self.santiago.save()

        with self.assertRaises(CommandError) as ctx:
            ejecutar("Medina-Novoa Martin\tmartin@test.com\t30011555\n", confirmar=True)
        self.assertIn("ya pertenecen a otro recurso", str(ctx.exception))
        self.martin.refresh_from_db()
        self.assertFalse(self.martin.nro_persona_sap)

    def test_un_correo_desconocido_detiene_todo(self):
        with self.assertRaises(CommandError) as ctx:
            ejecutar(
                "Medina-Novoa Martin\tmartin@test.com\t30011555\n"
                "Fulano De Tal\tfulano@test.com\t30019999\n",
                confirmar=True,
            )
        self.assertIn("fulano@test.com", str(ctx.exception))
        self.martin.refresh_from_db()
        self.assertFalse(self.martin.nro_persona_sap)

    def test_un_sap_que_no_es_numero_detiene_todo(self):
        with self.assertRaises(CommandError):
            ejecutar("Medina-Novoa Martin\tmartin@test.com\tABC123\n", confirmar=True)

    def test_una_linea_con_menos_columnas_detiene_todo(self):
        with self.assertRaises(CommandError) as ctx:
            ejecutar("Medina-Novoa Martin\tmartin@test.com\n", confirmar=True)
        self.assertIn("columnas", str(ctx.exception))

    # ── salvaguardas ────────────────────────────────────────────────────────

    def test_simular_no_escribe_nada(self):
        salida = ejecutar("Medina-Novoa Martin\tmartin@test.com\t30011555\n", simular=True)
        self.martin.refresh_from_db()
        self.assertFalse(self.martin.nro_persona_sap)
        self.assertIn("No se tocó nada", salida)

    def test_sin_simular_ni_confirmar_no_hace_nada(self):
        with self.assertRaises(CommandError):
            ejecutar("Medina-Novoa Martin\tmartin@test.com\t30011555\n")
        self.martin.refresh_from_db()
        self.assertFalse(self.martin.nro_persona_sap)
