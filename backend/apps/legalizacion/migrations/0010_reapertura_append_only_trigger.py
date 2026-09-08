"""Impide en la base de datos modificar o borrar una reapertura de día.

`ReaperturaDia` guarda quién deshizo unas firmas y por qué. Si esa entrada se
pudiera editar o borrar, no serviría para nada: justo quien tuviera motivos para
tapar una reapertura sería quien puede hacerlo.

El modelo ya lo bloquea con `AppendOnlyModel`; esto es la capa que sigue en pie
cuando el código se salta el ORM — un `UPDATE` desde el shell de producción, un
script de migración, o `psql` a pelo.

La función `impedir_cambios_append_only()` ya existe: la creó la migración
`assignments.0011` para `LogAuditoria`. Se usa la misma en vez de duplicarla,
pero se declara con `CREATE OR REPLACE` para que esta migración también funcione
sobre una base donde aquella no se haya aplicado todavía.
"""

from django.db import migrations

FUNCION = """
CREATE OR REPLACE FUNCTION impedir_cambios_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'La tabla % es append-only: % no esta permitido. Registra una entrada nueva.',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

CREAR = """
DROP TRIGGER IF EXISTS trg_reaperturadia_append_only ON legalizacion_reaperturadia;
CREATE TRIGGER trg_reaperturadia_append_only
    BEFORE UPDATE OR DELETE ON legalizacion_reaperturadia
    FOR EACH ROW EXECUTE FUNCTION impedir_cambios_append_only();
"""

QUITAR = """
DROP TRIGGER IF EXISTS trg_reaperturadia_append_only ON legalizacion_reaperturadia;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("legalizacion", "0009_reaperturadia"),
    ]

    operations = [
        # La funcion no se borra al revertir: la comparte `LogAuditoria` y
        # quitarla dejaria aquel disparador apuntando a nada.
        migrations.RunSQL(sql=FUNCION, reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(sql=CREAR, reverse_sql=QUITAR),
    ]
