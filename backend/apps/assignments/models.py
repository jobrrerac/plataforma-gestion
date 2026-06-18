from django.db import models
from django.contrib.auth.models import User
from apps.core.models import SoftDeleteModel, Recurso, Proyecto


class Asignacion(SoftDeleteModel):
    ESTADO_CHOICES = [
        ("SOLICITADA", "Solicitada"),
        ("APROBADA", "Aprobada"),
        ("RECHAZADA", "Rechazada"),
        ("REVOCADA", "Revocada"),
        ("INVALIDADA", "Invalidada"),
    ]
    POLITICA_CHOICES = [
        ("RECOMPUTAR", "Recomputar fecha fin (preserva horas)"),
        ("REDUCIR", "Reducir horas (preserva ventana)"),
    ]

    recurso = models.ForeignKey(Recurso, on_delete=models.PROTECT, related_name="asignaciones")
    proyecto = models.ForeignKey(Proyecto, on_delete=models.PROTECT, related_name="asignaciones")
    horas_totales = models.DecimalField(max_digits=7, decimal_places=2)
    intensidad_diaria = models.DecimalField(max_digits=4, decimal_places=2)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    politica_ausencia = models.CharField(max_length=20, choices=POLITICA_CHOICES, default="RECOMPUTAR")
    # Snapshots al momento de la aprobación
    tarifa_aplicada = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    costo_estimado = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="SOLICITADA")
    solicitada_por = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="asignaciones_solicitadas"
    )

    class Meta:
        verbose_name = "Asignación"
        verbose_name_plural = "Asignaciones"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.recurso} → {self.proyecto} [{self.estado}]"


class LogAuditoria(models.Model):
    """Registro append-only de cambios de estado en asignaciones. No editar ni borrar."""
    ACCION_CHOICES = [
        ("CREAR", "Crear"),
        ("APROBAR", "Aprobar"),
        ("RECHAZAR", "Rechazar"),
        ("REVOCAR", "Revocar"),
        ("INVALIDAR", "Invalidar"),
    ]
    asignacion = models.ForeignKey(Asignacion, on_delete=models.PROTECT, related_name="log")
    accion = models.CharField(max_length=20, choices=ACCION_CHOICES)
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    timestamp = models.DateTimeField(auto_now_add=True)
    detalle = models.JSONField(default=dict)

    class Meta:
        ordering = ["timestamp"]
        verbose_name = "Log de Auditoría"
        verbose_name_plural = "Logs de Auditoría"

    def __str__(self):
        return f"{self.accion} — {self.asignacion} ({self.timestamp:%Y-%m-%d %H:%M})"
