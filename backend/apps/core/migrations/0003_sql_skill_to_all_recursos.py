from django.db import migrations


def add_sql_skill(apps, schema_editor):
    Skill = apps.get_model("core", "Skill")
    Recurso = apps.get_model("core", "Recurso")
    sql_skill, _ = Skill.objects.get_or_create(nombre="SQL")
    for recurso in Recurso.objects.filter(deleted_at__isnull=True):
        recurso.skills.add(sql_skill)


class Migration(migrations.Migration):
    dependencies = [("core", "0002_skill_recurso_skills")]
    operations = [migrations.RunPython(add_sql_skill, migrations.RunPython.noop)]
