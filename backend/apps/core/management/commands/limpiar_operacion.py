"""Deja la plataforma sin datos de operación, para empezar a cargar los reales.

Borra asignaciones, horas legalizadas y novedades. **No toca** el maestro
—recursos, proyectos, tarifas, cuentas— ni el rastro de auditoría.

Por qué la auditoría se queda:

`LogAuditoria` es append-only por decisión del proyecto, y desde hoy lo impone
también un disparador de PostgreSQL. Un rastro que se puede borrar no es un
rastro. Además `LogAuditoria.asignacion` es `PROTECT`, así que un borrado físico
de asignaciones fallaría mientras existan sus entradas.

Las entidades con soft-delete se marcan como borradas, que es lo que el proyecto
entiende por "borrar": desaparecen de la aplicación y las constraints de
unicidad las ignoran, así que las fechas y códigos quedan libres para los datos
reales. Las que no lo tienen se borran de verdad porque no hay otra opción.

Uso:
    python manage.py limpiar_operacion --simular
    python manage.py limpiar_operacion --confirmar
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.assignments.models import Asignacion, CesionHoras, LiberacionRecurso, LogAuditoria
from apps.calendar_engine.models import Indisponibilidad
from apps.core.models import Proyecto, Recurso, TarifaVigente
from apps.legalizacion.models import DiaLegalizado, RegistroHoras

# El orden importa: lo que apunta a una asignación va antes que ella.
# (recuadro, modelo, soft_delete)
A_BORRAR = [
    ("Registros de horas", RegistroHoras, True),
    ("Días legalizados", DiaLegalizado, True),
    ("Novedades (vacaciones, permisos)", Indisponibilidad, True),
    ("Cesiones de horas", CesionHoras, False),
    ("Liberaciones de recurso", LiberacionRecurso, False),
    ("Asignaciones", Asignacion, True),
]

INTACTOS = [
    ("Recursos", Recurso),
    ("Proyectos", Proyecto),
    ("Tarifas vigentes", TarifaVigente),
    ("Auditoría", LogAuditoria),
]


class Command(BaseCommand):
    help = "Borra los datos de operación (asignaciones, horas, novedades). Conserva el maestro y la auditoría."

    def add_arguments(self, parser):
        parser.add_argument(
            "--simular", action="store_true",
            help="muestra qué se borraría, sin tocar nada",
        )
        parser.add_argument(
            "--confirmar", action="store_true",
            help="obligatorio para borrar de verdad",
        )

    def handle(self, *args, **opciones):
        simular = opciones["simular"]
        if not simular and not opciones["confirmar"]:
            raise CommandError(
                "Esto borra datos. Usa --simular para ver qué caería, "
                "o --confirmar para hacerlo."
            )

        self.stdout.write("")
        self.stdout.write("SE BORRA:")
        total = 0
        for etiqueta, modelo, soft in A_BORRAR:
            n = modelo.objects.count()
            total += n
            modo = "marcar como borrado" if soft else "borrado fisico"
            self.stdout.write(f"  {etiqueta:34} {n:4}   ({modo})")

        self.stdout.write("")
        self.stdout.write("NO SE TOCA:")
        for etiqueta, modelo in INTACTOS:
            gestor = getattr(modelo, "all_objects", modelo.objects)
            self.stdout.write(f"  {etiqueta:34} {gestor.count():4}")

        if simular:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"Simulacro: {total} filas afectadas. No se toco nada."))
            return

        with transaction.atomic():
            for etiqueta, modelo, soft in A_BORRAR:
                if soft:
                    # `queryset.delete()` haria un DELETE fisico: el soft-delete
                    # es un metodo de instancia. Es el mismo motivo por el que
                    # el admin necesita su propio mixin.
                    n = 0
                    for objeto in modelo.objects.all():
                        objeto.delete()
                        n += 1
                else:
                    n, _ = modelo.objects.all().delete()
                self.stdout.write(f"  {etiqueta:34} {n:4} listo")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Operación limpia. El maestro y la auditoría siguen intactos."))
