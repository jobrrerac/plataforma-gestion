"""Impide en la base de datos modificar o borrar una tarifa ya registrada.

El modelo ya lo bloquea, pero el ORM se puede saltar: una consulta cruda, un
`psql` contra producción, una herramienta de migración o una integración futura
escriben directamente sobre la tabla. El disparador es lo único que sigue en pie
en todos esos casos.

Editar una tarifa histórica cambia costos ya reportados y además **no dispara el
recomputo** —`signals.py` solo reacciona al alta—, así que las asignaciones se
quedarían con el costo viejo sin que nada avisara. Una corrección se registra
como una vigencia nueva.

Nota para migraciones futuras que necesiten tocar estos datos: hay que
desactivar el disparador explícitamente dentro de la propia migración
(`ALTER TABLE core_tarifavigente DISABLE TRIGGER ...`). Que cueste es
intencionado.
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
DROP TRIGGER IF EXISTS trg_tarifavigente_append_only ON core_tarifavigente;
CREATE TRIGGER trg_tarifavigente_append_only
    BEFORE UPDATE OR DELETE ON core_tarifavigente
    FOR EACH ROW EXECUTE FUNCTION impedir_cambios_append_only();
"""

QUITAR = """
DROP TRIGGER IF EXISTS trg_tarifavigente_append_only ON core_tarifavigente;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_proyecto_facturable"),
    ]

    operations = [
        # La función es compartida con assignments_logauditoria. `CREATE OR
        # REPLACE` la hace idempotente, así que no importa cuál de las dos
        # migraciones corra primero.
        migrations.RunSQL(sql=FUNCION, reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(sql=CREAR, reverse_sql=QUITAR),
    ]
