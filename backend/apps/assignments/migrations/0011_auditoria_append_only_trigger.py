"""Impide en la base de datos modificar o borrar una entrada de auditoría.

`LogAuditoria` se declaraba append-only desde el primer día, pero la regla solo
se aplicaba en el admin. Eso deja fuera el shell de producción, un script, una
integración futura y al próximo que escriba `.objects.update(...)` sin saber que
no debía.

Un rastro de auditoría que se puede reescribir no es un rastro de auditoría: si
alguien puede borrar la línea que dice quién aprobó qué, el registro deja de
servir justo para lo que existe.

El modelo ya lo bloquea; esto es la capa que sigue en pie cuando el código se
salta el ORM.
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
DROP TRIGGER IF EXISTS trg_logauditoria_append_only ON assignments_logauditoria;
CREATE TRIGGER trg_logauditoria_append_only
    BEFORE UPDATE OR DELETE ON assignments_logauditoria
    FOR EACH ROW EXECUTE FUNCTION impedir_cambios_append_only();
"""

QUITAR = """
DROP TRIGGER IF EXISTS trg_logauditoria_append_only ON assignments_logauditoria;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("assignments", "0010_liberacion_estado_revision"),
    ]

    operations = [
        # Función compartida con core_tarifavigente; `CREATE OR REPLACE` la hace
        # idempotente sin importar el orden en que corran las dos migraciones.
        migrations.RunSQL(sql=FUNCION, reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(sql=CREAR, reverse_sql=QUITAR),
    ]
