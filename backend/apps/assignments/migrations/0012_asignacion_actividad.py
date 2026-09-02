"""La actividad del cronograma deja de vivir enterrada en el log de auditoría.

`cargar_asignaciones` guardaba la tarea ("Integración Oracle") dentro de
`LogAuditoria.detalle["actividad"]` porque no había dónde ponerla. Eso trae dos
problemas.

El de fondo es de modelado: `LogAuditoria` es append-only porque registra
**eventos**, no estado actual. Si mañana se corrige el nombre de una tarea no se
puede actualizar — habría que añadir otra entrada, y entonces quedan dos filas
sin forma de saber cuál manda. El libro de actas no es sitio para un dato que
cambia.

El práctico es de cobertura: solo tienen actividad las asignaciones creadas por
la carga masiva. Las que se dan de alta desde la pantalla no llevan ninguna, así
que como dato para contrastar lo planificado con lo declarado era inservible.

Esta migración añade el campo y rellena hacia atrás lo que ya está en el log.
Nada se borra de `LogAuditoria`: el evento sigue contando lo que contaba.
"""

from django.db import migrations, models


def rellenar_desde_el_log(apps, schema_editor):
    """Copia la actividad que las cargas masivas dejaron en el log de auditoría.

    Se lee la entrada de creación de cada asignación. Si hubiera más de una
    —no debería, pero el log es append-only y nada lo impide— gana la más
    antigua, que es la del alta.
    """
    Asignacion = apps.get_model("assignments", "Asignacion")
    LogAuditoria = apps.get_model("assignments", "LogAuditoria")

    actividades = {}
    entradas = (
        LogAuditoria.objects
        .filter(accion="CREAR")
        .order_by("timestamp")
        .values_list("asignacion_id", "detalle")
    )
    for asignacion_id, detalle in entradas:
        actividad = (detalle or {}).get("actividad")
        if actividad and asignacion_id not in actividades:
            actividades[asignacion_id] = str(actividad)[:200]

    for asignacion_id, actividad in actividades.items():
        Asignacion.objects.filter(pk=asignacion_id).update(actividad=actividad)


def vaciar(apps, schema_editor):
    """Al revertir solo se vacía el campo: el log conserva el dato original."""
    Asignacion = apps.get_model("assignments", "Asignacion")
    Asignacion.objects.update(actividad="")


class Migration(migrations.Migration):
    dependencies = [
        ("assignments", "0011_auditoria_append_only_trigger"),
    ]

    operations = [
        migrations.AddField(
            model_name="asignacion",
            name="actividad",
            field=models.CharField(
                blank=True,
                max_length=200,
                verbose_name="Actividad",
                help_text=(
                    "Tarea del cronograma que cubre esta asignación (ej: «Integración "
                    "Oracle»). Opcional. Sirve para contrastar lo planificado con lo "
                    "que la persona declara después al legalizar sus horas."
                ),
            ),
        ),
        migrations.RunPython(rellenar_desde_el_log, vaciar),
    ]
