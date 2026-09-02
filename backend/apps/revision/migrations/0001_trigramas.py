"""Búsqueda de precedentes por parecido de texto.

Habilita `pg_trgm` e indexa `RegistroHoras.detalle`. Con eso, dado un renglón se
pueden encontrar los que esa misma persona —o ese mismo proyecto— declararon
antes con un texto parecido, que es lo que le falta a quien aprueba para saber
si ocho horas en algo son muchas o son lo de siempre.

Es la mitad léxica de la búsqueda híbrida del diseño. La otra mitad, la
vectorial, necesita un modelo de embeddings y espera a que se decida dónde corre;
sin esa decisión habría una columna de vectores sin nada que la llene.

El índice es GIN sobre trigramas: sin él, `similarity()` recorre la tabla entera
en cada consulta. Con 16.000 filas al año no se notaría hoy, pero esta consulta
sale en una pantalla que ya carga cien renglones.

Vive en `apps.revision` y no en `legalizacion` a propósito: todo lo del triaje
junto, para que apagar el módulo siga siendo quitar una app de INSTALLED_APPS.
"""

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("legalizacion", "0007_rellenar_firma_de_dias_aprobados"),
    ]

    operations = [
        TrigramExtension(),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX IF NOT EXISTS registrohoras_detalle_trgm "
                "ON legalizacion_registrohoras USING gin (detalle gin_trgm_ops);"
            ),
            reverse_sql="DROP INDEX IF EXISTS registrohoras_detalle_trgm;",
        ),
    ]
