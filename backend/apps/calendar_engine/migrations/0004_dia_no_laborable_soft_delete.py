"""`DiaNoLaborable` pasa a soft-delete, como el resto de entidades.

Heredaba de `models.Model`, así que un DELETE por API o desde el admin lo
borraba de verdad. Con él desaparecía la razón por la que un día no computó en
asignaciones ya aprobadas, y con ella la trazabilidad de por qué una ventana
acabó cuando acabó.

Dos detalles que no salen del autodetector:

- `created_at` se rellena con `creado_en`, que es la fecha real de creación.
  Poner `now()` habría inventado un dato en todas las filas existentes.
- La unicidad de `fecha` pasa a ser condicional. Con `unique=True` a secas, un
  día eliminado seguiría ocupando su fecha para siempre y volver a darlo de alta
  fallaría sin explicación visible.
"""

from django.db import migrations, models
from django.utils import timezone


def copiar_fecha_de_creacion(apps, schema_editor):
    DiaNoLaborable = apps.get_model("calendar_engine", "DiaNoLaborable")
    for dia in DiaNoLaborable.objects.all():
        DiaNoLaborable.objects.filter(pk=dia.pk).update(
            created_at=dia.creado_en, updated_at=dia.creado_en,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("calendar_engine", "0003_alter_indisponibilidad_tipo"),
    ]

    operations = [
        migrations.AddField(
            model_name="dianolaborable",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="dianolaborable",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="dianolaborable",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(copiar_fecha_de_creacion, migrations.RunPython.noop),
        # La unicidad simple se sustituye por una que ignora lo borrado.
        migrations.AlterField(
            model_name="dianolaborable",
            name="fecha",
            field=models.DateField(),
        ),
        migrations.AddConstraint(
            model_name="dianolaborable",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("fecha",),
                name="un_dia_no_laborable_por_fecha",
            ),
        ),
    ]
