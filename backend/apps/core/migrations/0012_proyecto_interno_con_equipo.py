"""Proyectos internos a los que se asigna gente.

Hasta ahora «interno» significaba una sola cosa: no facturable, sin equipo, y
todo el mundo podía imputarle horas. Sirve para gestión departamental, pero no
para un acelerador o un producto propio, donde hay cinco personas trabajando y
hace falta poder planificarlas.

Esta marca separa los dos casos. Nulo de riesgo: por defecto `False`, así que
todos los proyectos existentes —internos incluidos— siguen comportándose igual.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_proyecto_aprobador_delegado"),
    ]

    operations = [
        migrations.AddField(
            model_name="proyecto",
            name="interno_con_equipo",
            field=models.BooleanField(
                default=False,
                verbose_name="Interno con equipo",
                help_text=(
                    "Proyecto interno al que se asigna gente: aceleradores y productos "
                    "propios, no gestión departamental. Marca tres diferencias — sus "
                    "asignaciones NO ocupan capacidad, así que la persona sigue "
                    "apareciendo disponible para cliente; CEDEN cuando se aprueba una "
                    "asignación de cliente que se solape, quedando en 0 esos días; y "
                    "solo quien esté asignado puede imputarle horas. Solo tiene sentido "
                    "en proyectos no facturables."
                ),
            ),
        ),
    ]
