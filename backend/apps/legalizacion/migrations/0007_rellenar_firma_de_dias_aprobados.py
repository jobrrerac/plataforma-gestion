"""Rellena la firma de los días que quedaron aprobados sin firmante.

Al bajar la aprobación del día al renglón, `recalcular_estado` dejó de poner
`aprobado_por`. Los días aprobados a partir de entonces quedaron en estado
APROBADO con el campo a nulo, y la pantalla de legalización —que muestra
"Aprobado por ..."— devolvía un **500** al abrirlos.

El detalle de por qué reventaba: el argumento de `|default:` se evalúa siempre,
lo use o no, así que `dia.aprobado_por.username` con el campo a nulo lanza
`VariableDoesNotExist` y se lleva la página entera por delante.

La firma se toma del último renglón aprobado, que es quien de hecho cerró el
día. Los días que no tengan ningún renglón con fecha de aprobación se quedan sin
firma: la plantilla ya lo tolera, y no hay dato del que sacarla.
"""

from django.db import migrations


def rellenar_firma(apps, schema_editor):
    DiaLegalizado = apps.get_model("legalizacion", "DiaLegalizado")
    RegistroHoras = apps.get_model("legalizacion", "RegistroHoras")

    for dia in DiaLegalizado.objects.filter(estado="APROBADO", aprobado_por__isnull=True):
        ultimo = (
            RegistroHoras.objects
            .filter(dia=dia, aprobado_en__isnull=False)
            .order_by("-aprobado_en")
            .first()
        )
        if ultimo is None:
            continue
        DiaLegalizado.objects.filter(pk=dia.pk).update(
            aprobado_por_id=ultimo.aprobado_por_id, aprobado_en=ultimo.aprobado_en,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("legalizacion", "0006_traspasar_aprobacion_al_renglon"),
    ]

    operations = [
        # Sin marcha atrás: vaciar la firma volvería a dejar el 500 servido.
        migrations.RunPython(rellenar_firma, migrations.RunPython.noop),
    ]
