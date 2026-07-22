# Generated manually (mirrors makemigrations output; container FS is read-only).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assignments', '0009_liberacionrecurso_alter_logauditoria_accion'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameField(
            model_name='liberacionrecurso',
            old_name='creado_por',
            new_name='solicitada_por',
        ),
        migrations.AlterField(
            model_name='liberacionrecurso',
            name='solicitada_por',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='liberaciones_solicitadas', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='liberacionrecurso',
            name='estado',
            field=models.CharField(choices=[('SOLICITADA', 'Solicitada'), ('APROBADA', 'Aprobada'), ('RECHAZADA', 'Rechazada'), ('ANULADA', 'Anulada')], default='SOLICITADA', max_length=20),
        ),
        migrations.AddField(
            model_name='liberacionrecurso',
            name='revisada_por',
            field=models.ForeignKey(blank=True, help_text='Admin que aprobó o rechazó la solicitud.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='liberaciones_revisadas', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='liberacionrecurso',
            name='revisada_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='logauditoria',
            name='accion',
            field=models.CharField(choices=[('CREAR', 'Crear'), ('APROBAR', 'Aprobar'), ('RECHAZAR', 'Rechazar'), ('REVOCAR', 'Revocar'), ('INVALIDAR', 'Invalidar'), ('CEDER', 'Ceder horas'), ('ANULAR_CESION', 'Anular cesión'), ('SOLICITAR_LIBERACION', 'Solicitar liberación'), ('LIBERAR', 'Aprobar liberación'), ('RECHAZAR_LIBERACION', 'Rechazar liberación'), ('ANULAR_LIBERACION', 'Anular liberación'), ('RECOMPUTO_TARIFA', 'Recomputo por cambio de tarifa')], max_length=20),
        ),
    ]
