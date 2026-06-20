from django.db import migrations, models
import django.db.models.deletion


def copy_m2m_to_recursosskill(apps, schema_editor):
    """Copy existing implicit M2M rows to the new through model, defaulting suficiencia=3."""
    RecursoSkill = apps.get_model("core", "RecursoSkill")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT recurso_id, skill_id FROM core_recurso_skills")
        rows = cursor.fetchall()
    for recurso_id, skill_id in rows:
        RecursoSkill.objects.get_or_create(
            recurso_id=recurso_id,
            skill_id=skill_id,
            defaults={"suficiencia": 3},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_sql_skill_to_all_recursos"),
    ]

    operations = [
        # 1. Add description field to Skill
        migrations.AddField(
            model_name="skill",
            name="descripcion",
            field=models.CharField(
                blank=True,
                help_text="Qué capacidades aporta este skill (máx. 300 caracteres).",
                max_length=300,
            ),
        ),
        # 2. Create through model table
        migrations.CreateModel(
            name="RecursoSkill",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "suficiencia",
                    models.PositiveSmallIntegerField(
                        choices=[
                            (1, "★ Básico"),
                            (2, "★★ Elemental"),
                            (3, "★★★ Intermedio"),
                            (4, "★★★★ Avanzado"),
                            (5, "★★★★★ Experto - Certificado"),
                        ],
                        default=3,
                        help_text="Nivel de dominio: 1 básico → 5 experto.",
                    ),
                ),
                (
                    "recurso",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recurso_skills",
                        to="core.recurso",
                    ),
                ),
                (
                    "skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recurso_skills",
                        to="core.skill",
                    ),
                ),
            ],
            options={
                "verbose_name": "Skill de recurso",
                "verbose_name_plural": "Skills de recurso",
                "ordering": ["-suficiencia", "skill__nombre"],
                "unique_together": {("recurso", "skill")},
            },
        ),
        # 3. Copy existing M2M data into the through model
        migrations.RunPython(copy_m2m_to_recursosskill, migrations.RunPython.noop),
        # 4. Remove the implicit M2M table (drops core_recurso_skills)
        migrations.RemoveField(
            model_name="recurso",
            name="skills",
        ),
        # 5. Re-add the field pointing to the through model (no-op at DB level)
        migrations.AddField(
            model_name="recurso",
            name="skills",
            field=models.ManyToManyField(
                blank=True,
                related_name="recursos",
                through="core.RecursoSkill",
                to="core.skill",
            ),
        ),
    ]
