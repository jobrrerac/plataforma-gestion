"""Aprobador delegado por proyecto.

Separa la capacidad de aprobar del rol de quien la ejerce. Hasta ahora aprobar
horas dependía de ser PM del proyecto o Admin; con esto un proyecto puede
nombrar a alguien concreto —un ingeniero, otro PM, quien sea— y esa designación
**es** la autorización, sin necesidad de cambiarle el grupo.

Nulo por defecto: sin delegado, todo funciona como antes.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0010_tarifa_append_only_trigger"),
    ]

    operations = [
        migrations.AddField(
            model_name="proyecto",
            name="aprobador_delegado",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="proyectos_aprobador",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Aprobador delegado",
                help_text=(
                    "Quien puede aprobar las horas de este proyecto ademas del PM. "
                    "Puede ser cualquiera —un ingeniero, un admin, otro PM—: la "
                    "delegacion ES la autorizacion, no hace falta que tenga un rol "
                    "concreto. No da acceso a costos ni a nada mas del proyecto."
                ),
            ),
        ),
    ]
