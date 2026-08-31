"""Las entidades append-only lo son de verdad, no solo en el docstring.

Hallazgo de la revisión de seguridad: `LogAuditoria` y `TarifaVigente` se
declaraban append-only desde el primer día, pero la regla solo se aplicaba en el
admin de Django. Eso deja fuera el shell de producción, un script, una
integración futura y al próximo que escriba `.objects.update(...)` sin saber que
no debía.

Lo que estaba en juego en cada caso:

- **LogAuditoria**: un rastro que se puede reescribir no es un rastro. Si alguien
  puede borrar la línea que dice quién aprobó qué, el registro deja de servir
  justo para lo que existe.
- **TarifaVigente**: editar una tarifa histórica cambia costos ya reportados, y
  además **no dispara el recomputo** (`signals.py` solo reacciona al alta), así
  que las asignaciones se quedarían con el costo viejo sin que nada avisara.

Hay tres capas y se prueban las tres: el modelo, el queryset y el disparador de
PostgreSQL. La última es la que importa cuando el código se salta el ORM.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection, transaction
from django.test import TestCase

from apps.assignments.models import Asignacion, LogAuditoria
from apps.core.models import AppendOnlyError, Proyecto, Recurso, TarifaVigente


class TarifaAppendOnlyTests(TestCase):
    def setUp(self):
        self.recurso = Recurso.objects.create(
            nombre="DevTarifa", email="devtarifa@test.com", banda="SR",
        )
        self.tarifa = TarifaVigente.objects.create(
            recurso=self.recurso, valor_hora=Decimal("10.00"), fecha_desde=date(2025, 1, 1),
        )

    def test_se_pueden_registrar_vigencias_nuevas(self):
        """Lo que sí tiene que seguir funcionando: corregir es añadir."""
        TarifaVigente.objects.create(
            recurso=self.recurso, valor_hora=Decimal("12.00"), fecha_desde=date(2025, 6, 1),
        )
        self.assertEqual(TarifaVigente.objects.filter(recurso=self.recurso).count(), 2)

    def test_no_se_edita_una_tarifa_registrada(self):
        self.tarifa.valor_hora = Decimal("999.00")
        with self.assertRaises(AppendOnlyError):
            self.tarifa.save()

    def test_no_se_borra_una_tarifa(self):
        with self.assertRaises(AppendOnlyError):
            self.tarifa.delete()

    def test_no_se_actualiza_en_bloque(self):
        # `Model.save()` no se entera de un `update()` de queryset: baja directo
        # a SQL. Sin cerrar esta puerta, la protección del modelo sería una
        # valla con la verja abierta al lado.
        with self.assertRaises(AppendOnlyError):
            TarifaVigente.objects.filter(pk=self.tarifa.pk).update(valor_hora=Decimal("1.00"))

    def test_no_se_borra_en_bloque(self):
        with self.assertRaises(AppendOnlyError):
            TarifaVigente.objects.filter(pk=self.tarifa.pk).delete()

    def test_la_base_de_datos_tambien_lo_impide(self):
        """La capa que sigue en pie cuando alguien se salta el ORM."""
        with self.assertRaises(Exception) as ctx, transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE core_tarifavigente SET valor_hora = 1 WHERE id = %s",
                    [self.tarifa.pk],
                )
        self.assertIn("append-only", str(ctx.exception).lower())


class AuditoriaAppendOnlyTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root_ao", "rao@test.com", "pass")
        self.recurso = Recurso.objects.create(
            nombre="DevAudit", email="devaudit@test.com", banda="SR",
        )
        self.proyecto = Proyecto.objects.create(
            codigo="P-AUDIT", nombre="A", cliente="X",
            fecha_inicio=date(2025, 1, 1), pm=self.admin,
        )
        self.asignacion = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2025, 1, 13), fecha_fin=date(2025, 1, 17),
            dias_habiles=5, horas_totales=40, intensidad_diaria=8,
            estado="SOLICITADA", solicitada_por=self.admin,
        )
        self.log = LogAuditoria.objects.create(
            asignacion=self.asignacion, accion="CREAR", actor=self.admin, detalle={},
        )

    def test_se_pueden_seguir_registrando_entradas(self):
        LogAuditoria.objects.create(
            asignacion=self.asignacion, accion="APROBAR", actor=self.admin, detalle={},
        )
        self.assertEqual(LogAuditoria.objects.filter(asignacion=self.asignacion).count(), 2)

    def test_no_se_edita_una_entrada(self):
        self.log.accion = "APROBAR"
        with self.assertRaises(AppendOnlyError):
            self.log.save()

    def test_no_se_borra_una_entrada(self):
        with self.assertRaises(AppendOnlyError):
            self.log.delete()

    def test_no_se_reescribe_quien_aprobo(self):
        """El caso que da sentido a todo esto."""
        otro = User.objects.create_user("otro_actor", password="x")
        with self.assertRaises(AppendOnlyError):
            LogAuditoria.objects.filter(pk=self.log.pk).update(actor=otro)

    def test_no_se_borra_el_rastro_en_bloque(self):
        with self.assertRaises(AppendOnlyError):
            LogAuditoria.objects.filter(asignacion=self.asignacion).delete()

    def test_la_base_de_datos_tambien_lo_impide(self):
        with self.assertRaises(Exception) as ctx, transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("DELETE FROM assignments_logauditoria WHERE id = %s", [self.log.pk])
        self.assertIn("append-only", str(ctx.exception).lower())
