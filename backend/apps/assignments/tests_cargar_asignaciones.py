"""Carga masiva de solicitudes de recurso desde un plan de trabajo.

Cargar un cronograma a mano es lento y, sobre todo, silencioso cuando sale mal:
una asignación a la persona equivocada se ve igual que una correcta. Por eso lo
que se prueba aquí no es tanto el camino feliz como los sitios donde el comando
tiene que **negarse a adivinar**:

- un nombre ambiguo entre dos personas con apellidos parecidos,
- una fila repetida en el mismo plan, que la comprobación contra la base de
  datos no vería porque dentro de la transacción aún no existe la primera,
- volver a cargar el plan entero, que no debe duplicar lo ya cargado.

Y una cosa que sí es del camino feliz pero se pierde fácil: `intensidad_diaria`
guarda un solo decimal, así que repartir 8,5 h en dos días no da 4,25 sino 4,3.
El comando redondea y lo dice; la prueba fija ese comportamiento para que nadie
lo "arregle" callándolo.
"""

import sys
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.assignments.models import Asignacion, LogAuditoria
from apps.core.models import Proyecto, Recurso

# 2026: el 31/08 es lunes y el 04/09 viernes, así que el rango tiene 5 días
# hábiles y ningún feriado colombiano en medio.
PLAN_BASE = (
    "Daniel Guzman\tPrecarga de datos\t31/08/2026\t04/09/2026\t30\n"
)


def ejecutar(plan, **extra):
    """Corre el comando leyendo `plan` de la entrada estándar; devuelve su salida."""
    salida = StringIO()
    opciones = {
        "proyecto": "V-25999999/Q",
        "solicitante": "pm_prueba",
        "stdout": salida,
        "stderr": salida,
        **extra,
    }
    with patch.object(sys, "stdin", StringIO(plan)):
        call_command("cargar_asignaciones", "-", **opciones)
    return salida.getvalue()


class CargarAsignacionesTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm_prueba", "pm@test.com", "x")
        self.proyecto = Proyecto.objects.create(
            codigo="V-25999999/Q", nombre="Proyecto de prueba", cliente="ACME",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        self.daniel = Recurso.objects.create(
            nombre="Guzman-Mejia Daniel-Fernando",
            email="daniel.guzman@test.com", banda="SR",
        )
        self.martin = Recurso.objects.create(
            nombre="Medina-Novoa Martin", email="martin.medina@test.com", banda="SSR",
        )

    # ── reparto de horas ────────────────────────────────────────────────────

    def test_reparte_las_horas_totales_entre_los_dias_habiles(self):
        ejecutar(PLAN_BASE, confirmar=True)

        asignacion = Asignacion.objects.get(recurso=self.daniel)
        self.assertEqual(asignacion.estado, "SOLICITADA")
        self.assertEqual(asignacion.dias_habiles, 5)
        # 30 h en 5 días hábiles = 6 h/día, no 30 h/día.
        self.assertEqual(asignacion.intensidad_diaria, Decimal("6.0"))
        self.assertEqual(asignacion.horas_totales, 30)

    def test_redondea_a_un_decimal_y_lo_avisa(self):
        """8,5 h en 2 días son 4,25 h/día, que el campo no sabe guardar."""
        plan = "Martín Medina\tIntegración\t14/09/2026\t15/09/2026\t8,5\n"
        salida = ejecutar(plan, confirmar=True)

        asignacion = Asignacion.objects.get(recurso=self.martin)
        self.assertEqual(asignacion.intensidad_diaria, Decimal("4.3"))
        self.assertIn("ajuste de redondeo", salida)
        self.assertIn("+0.1", salida)

    def test_acepta_la_coma_como_decimal(self):
        plan = "Martín Medina\tTarea\t14/09/2026\t14/09/2026\t8,5\n"
        ejecutar(plan, confirmar=True)
        self.assertEqual(
            Asignacion.objects.get(recurso=self.martin).intensidad_diaria,
            Decimal("8.5"),
        )

    def test_las_crea_solicitadas_nunca_aprobadas(self):
        """El comando carga trabajo; aprobarlo sigue siendo un acto de alguien."""
        ejecutar(PLAN_BASE, confirmar=True)
        self.assertFalse(Asignacion.objects.filter(estado="APROBADA").exists())

    def test_guarda_la_actividad_en_el_log(self):
        """`Asignacion` no tiene campo de tarea: si no va al log, se pierde."""
        ejecutar(PLAN_BASE, confirmar=True)
        log = LogAuditoria.objects.get(accion="CREAR")
        self.assertEqual(log.detalle["actividad"], "Precarga de datos")
        self.assertEqual(log.detalle["origen"], "carga_masiva")
        self.assertEqual(log.detalle["horas_pedidas"], 30.0)

    # ── negarse a adivinar ──────────────────────────────────────────────────

    def test_un_nombre_ambiguo_detiene_todo(self):
        """Dos apellidos Martinez: asignarle el trabajo al que salga primero
        sería peor que fallar."""
        Recurso.objects.create(
            nombre="Martinez-Forero Daniel-Santiago",
            email="d.martinez@test.com", banda="JR",
        )
        Recurso.objects.create(
            nombre="Martinez-Herrera Santiago-Orlando",
            email="s.martinez@test.com", banda="JR",
        )
        plan = "Santiago Martinez\tTarea\t14/09/2026\t14/09/2026\t4\n"
        with self.assertRaises(CommandError) as ctx:
            ejecutar(plan, confirmar=True)
        self.assertIn("coincide con 2 recursos", str(ctx.exception))
        self.assertEqual(Asignacion.objects.count(), 0)

    def test_un_nombre_mas_completo_desambigua(self):
        Recurso.objects.create(
            nombre="Martinez-Forero Daniel-Santiago",
            email="d.martinez@test.com", banda="JR",
        )
        esperado = Recurso.objects.create(
            nombre="Martinez-Herrera Santiago-Orlando",
            email="s.martinez@test.com", banda="JR",
        )
        plan = "Santiago Orlando Martinez\tTarea\t14/09/2026\t14/09/2026\t4\n"
        ejecutar(plan, confirmar=True)
        self.assertEqual(Asignacion.objects.get().recurso, esperado)

    def test_el_correo_siempre_desambigua(self):
        Recurso.objects.create(
            nombre="Guzman-Otro Daniel", email="otro.daniel@test.com", banda="JR",
        )
        plan = "daniel.guzman@test.com\tTarea\t14/09/2026\t14/09/2026\t4\n"
        ejecutar(plan, confirmar=True)
        self.assertEqual(Asignacion.objects.get().recurso, self.daniel)

    def test_un_recurso_inexistente_detiene_todo(self):
        plan = "Fulano De Tal\tTarea\t14/09/2026\t14/09/2026\t4\n"
        with self.assertRaises(CommandError):
            ejecutar(plan, confirmar=True)
        self.assertEqual(Asignacion.objects.count(), 0)

    def test_un_rango_sin_dias_habiles_detiene_todo(self):
        """19 y 20 de septiembre de 2026 son sábado y domingo."""
        plan = "Daniel Guzman\tTarea\t19/09/2026\t20/09/2026\t4\n"
        with self.assertRaises(CommandError) as ctx:
            ejecutar(plan, confirmar=True)
        self.assertIn("día hábil", str(ctx.exception))

    # ── no duplicar ─────────────────────────────────────────────────────────

    def test_no_duplica_lo_que_ya_existe(self):
        ejecutar(PLAN_BASE, confirmar=True)
        salida = ejecutar(PLAN_BASE, confirmar=True)

        self.assertEqual(Asignacion.objects.count(), 1)
        self.assertIn("ya existe", salida)

    def test_no_duplica_dentro_del_mismo_plan(self):
        """La comprobación contra la base no ve la fila que aún no se ha escrito."""
        plan = PLAN_BASE + PLAN_BASE
        salida = ejecutar(plan, confirmar=True)
        self.assertEqual(Asignacion.objects.count(), 1)
        self.assertIn("repetida en el plan", salida)

    def test_una_rechazada_no_bloquea_volver_a_pedirla(self):
        ejecutar(PLAN_BASE, confirmar=True)
        Asignacion.objects.update(estado="RECHAZADA")

        ejecutar(PLAN_BASE, confirmar=True)
        self.assertEqual(Asignacion.objects.filter(estado="SOLICITADA").count(), 1)

    # ── salvaguardas del comando ────────────────────────────────────────────

    def test_simular_no_escribe_nada(self):
        salida = ejecutar(PLAN_BASE, simular=True)
        self.assertEqual(Asignacion.objects.count(), 0)
        self.assertIn("No se tocó nada", salida)

    def test_sin_simular_ni_confirmar_no_hace_nada(self):
        with self.assertRaises(CommandError):
            ejecutar(PLAN_BASE)
        self.assertEqual(Asignacion.objects.count(), 0)

    def test_un_proyecto_cerrado_no_admite_carga(self):
        self.proyecto.estado = "CERRADO"
        self.proyecto.save()
        with self.assertRaises(CommandError):
            ejecutar(PLAN_BASE, confirmar=True)

    def test_avisa_de_los_dias_que_pasarian_la_jornada(self):
        """No bloquea —la pantalla tampoco lo hace— pero no deja que sorprenda
        de a una al ir aprobando."""
        plan = (
            "Daniel Guzman\tTarea A\t14/09/2026\t14/09/2026\t6\n"
            "Daniel Guzman\tTarea B\t14/09/2026\t15/09/2026\t6\n"
        )
        salida = ejecutar(plan, simular=True)
        self.assertIn("superarían la jornada", salida)
        self.assertIn("14/09/2026", salida)


class ReemplazarUnPlanTests(TestCase):
    """Cuando el cronograma cambia entero.

    Sin retirar lo anterior, el plan nuevo se SUMA al viejo y la persona acaba
    con el doble de horas ese día: exactamente lo que la validación de capacidad
    rechazará al aprobar. Y como las asignaciones no se borran —se revocan o se
    rechazan— el rastro de lo que hubo antes se conserva.
    """

    def setUp(self):
        # El helper `ejecutar` usa este nombre y el codigo V-25999999/Q.
        self.pm = User.objects.create_user("pm_prueba", "pm@test.com", "clave-larga-123")
        self.proyecto = Proyecto.objects.create(
            codigo="V-25999999/Q", nombre="Simulador", cliente="ANECOOP",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        self.otro_proyecto = Proyecto.objects.create(
            codigo="V-25888888/Q", nombre="Otro", cliente="ANECOOP",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        self.daniel = Recurso.objects.create(
            nombre="Guzman-Mejia Daniel-Fernando",
            email="daniel.guzman@test.com", banda="SR",
        )
        self.ajeno = Recurso.objects.create(
            nombre="Paiba-Celeita Laura-Alejandra", email="laura@test.com", banda="JR",
        )

    def _vieja(self, recurso=None, proyecto=None, estado="APROBADA"):
        return Asignacion.objects.create(
            recurso=recurso or self.daniel, proyecto=proyecto or self.proyecto,
            modo_asignacion="RANGO",
            fecha_inicio=date(2026, 8, 31), fecha_fin=date(2026, 9, 4),
            dias_habiles=5, horas_totales=30, intensidad_diaria=Decimal("6.0"),
            estado=estado, solicitada_por=self.pm,
        )

    def test_retira_lo_aprobado_y_crea_lo_nuevo(self):
        vieja = self._vieja()
        ejecutar(PLAN_BASE, confirmar=True, reemplazar=True)

        vieja.refresh_from_db()
        self.assertEqual(vieja.estado, "REVOCADA")
        self.assertEqual(Asignacion.objects.filter(estado="SOLICITADA").count(), 1)

    def test_una_solicitada_se_rechaza(self):
        vieja = self._vieja(estado="SOLICITADA")
        ejecutar(PLAN_BASE, confirmar=True, reemplazar=True)

        vieja.refresh_from_db()
        self.assertEqual(vieja.estado, "RECHAZADA")

    def test_sin_la_bandera_no_retira_nada(self):
        """Y entonces la fila se omite por duplicada, que es el comportamiento
        de siempre."""
        vieja = self._vieja()
        salida = ejecutar(PLAN_BASE, confirmar=True)

        vieja.refresh_from_db()
        self.assertEqual(vieja.estado, "APROBADA")
        self.assertIn("ya existe", salida)

    def test_no_toca_otros_proyectos(self):
        """Esa persona puede estar en mas sitios: el plan solo habla de uno."""
        otra = self._vieja(proyecto=self.otro_proyecto)
        ejecutar(PLAN_BASE, confirmar=True, reemplazar=True)

        otra.refresh_from_db()
        self.assertEqual(otra.estado, "APROBADA")

    def test_no_toca_a_quien_no_esta_en_el_plan(self):
        """Si hay mas gente en ese proyecto por otra via, no es asunto del
        archivo que se esta cargando."""
        ajena = self._vieja(recurso=self.ajeno)
        ejecutar(PLAN_BASE, confirmar=True, reemplazar=True)

        ajena.refresh_from_db()
        self.assertEqual(ajena.estado, "APROBADA")

    def test_simular_no_retira_nada(self):
        vieja = self._vieja()
        salida = ejecutar(PLAN_BASE, simular=True, reemplazar=True)

        vieja.refresh_from_db()
        self.assertEqual(vieja.estado, "APROBADA")
        self.assertIn("SE RETIRA", salida)
        self.assertIn("No se tocó nada", salida)

    def test_no_avisa_de_choques_con_lo_que_se_va_a_retirar(self):
        """Seria una alarma falsa: eso deja de existir en la misma transaccion."""
        self._vieja()  # 6 h/dia en las mismas fechas del plan
        salida = ejecutar(PLAN_BASE, simular=True, reemplazar=True)
        self.assertNotIn("superarían la jornada", salida)

    def test_sin_la_bandera_ese_choque_si_se_avisa(self):
        """Fechas distintas pero solapadas: no es duplicado, asi que la fila se
        crea, y entonces si hay que avisar de que chocara al aprobar."""
        self._vieja()  # aprobada 31/08–04/09 a 6 h/dia
        plan = "Daniel Guzman\tOtra cosa\t01/09/2026\t03/09/2026\t15\n"
        salida = ejecutar(plan, simular=True)
        self.assertIn("superarían la jornada", salida)
