from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_cluster_recurso_nro_persona_sap_recurso_clusters_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="proyecto",
            name="codigo_pep",
            field=models.CharField(
                blank=True,
                help_text="Elemento PEP del proyecto en SAP (ej: P-2026-00123). Único cuando se informa.",
                max_length=50,
                null=True,
                unique=True,
                verbose_name="Código PEP",
            ),
        ),
    ]
