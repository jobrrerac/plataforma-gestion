# Generated manually (mirrors makemigrations output; container FS is read-only).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assignments', '0008_alter_logauditoria_accion_alter_logauditoria_actor'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='logauditoria',
            name='accion',
            field=models.CharField(choices=[('CREAR', 'Crear'), ('APROBAR', 'Aprobar'), ('RECHAZAR', 'Rechazar'), ('REVOCAR', 'Revocar'), ('INVALIDAR', 'Invalidar'), ('CEDER', 'Ceder horas'), ('ANULAR_CESION', 'Anular cesión'), ('LIBERAR', 'Liberar recurso'), ('ANULAR_LIBERACION', 'Anular liberación'), ('RECOMPUTO_TARIFA', 'Recomputo por cambio de tarifa')], max_length=20),
        ),
        migrations.CreateModel(
            name='LiberacionRecurso',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fecha_inicio', models.DateField(help_text='Primer día liberado (inclusive).')),
                ('fecha_fin', models.DateField(help_text='Último día liberado (inclusive).')),
                ('politica', models.CharField(choices=[('RECOMPUTAR', 'Recomputar fecha fin (preserva horas)'), ('REDUCIR', 'Reducir horas (preserva ventana)')], help_text='RECOMPUTAR: extiende fecha_fin para recuperar las horas. REDUCIR: baja el total de horas de la asignación.', max_length=20)),
                ('motivo', models.CharField(blank=True, help_text="Motivo de la liberación (ej: 'Cliente en vacaciones').", max_length=200)),
                ('dias_liberados', models.PositiveIntegerField(help_text='Días hábiles con carga que quedaron congelados en la ventana.')),
                ('horas_liberadas', models.DecimalField(decimal_places=1, help_text='Suma de horas de los días congelados.', max_digits=6)),
                ('fecha_fin_original', models.DateField(blank=True, help_text='fecha_fin de la asignación antes de recomputar (solo RECOMPUTAR).', null=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('anulada_en', models.DateTimeField(blank=True, null=True)),
                ('asignacion', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='liberaciones', to='assignments.asignacion')),
                ('creado_por', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='liberaciones_creadas', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Liberación de recurso',
                'verbose_name_plural': 'Liberaciones de recurso',
                'ordering': ['-creado_en'],
            },
        ),
    ]
