from django.db import models
from django.contrib.auth.models import User
from apps.core.models import SoftDeleteModel, Recurso, Proyecto, Cluster


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
    MODO_CHOICES = [
        ("HORAS", "Por horas totales"),
        ("DIAS", "Por días hábiles"),
        ("RANGO", "Por rango de fechas"),
    ]

    recurso = models.ForeignKey(Recurso, on_delete=models.PROTECT, related_name="asignaciones")
    proyecto = models.ForeignKey(Proyecto, on_delete=models.PROTECT, related_name="asignaciones")
    cluster = models.ForeignKey(
        Cluster, null=True, blank=True, on_delete=models.PROTECT,
        related_name="asignaciones", verbose_name="Cluster",
        help_text="Cluster del recurso bajo el que opera esta asignación.",
    )
    modo_asignacion = models.CharField(max_length=10, choices=MODO_CHOICES, default="HORAS", verbose_name="Modo")
    horas_totales = models.PositiveIntegerField(null=True, blank=True, help_text="Total de horas (ej: 40, 80, 160)")
    dias_habiles = models.PositiveIntegerField(null=True, blank=True, help_text="Días hábiles de trabajo")
    intensidad_diaria = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="Horas por día (ej: 4, 4.5, 8)")
    jornada_completa = models.BooleanField(default=False, help_text="El recurso trabaja su jornada máxima cada día del rango (lun–jue 8.5 h, vie 8 h)")
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
    # Solicitudes recurrentes: las asignaciones generadas por un mismo patrón
    # semanal (ej: "próximos 4 lunes, 4 h") comparten esta serie.
    serie = models.UUIDField(
        null=True, blank=True, db_index=True, verbose_name="Serie",
        help_text="Agrupa las asignaciones creadas por una misma solicitud recurrente.",
    )

    class Meta:
        verbose_name = "Asignación"
        verbose_name_plural = "Asignaciones"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.recurso} → {self.proyecto} [{self.estado}]"


class CesionHoras(models.Model):
    """
    Cesión de horas de un día concreto de una asignación APROBADA hacia otro
    proyecto (acuerdo entre PMs). La asignación original NUNCA se edita a mano:
    esta operación registra la excepción y sus efectos quedan en LogAuditoria.

    Ciclo de vida: se crea junto con una asignación destino SOLICITADA. Mientras
    el destino está pendiente, las horas cedidas quedan RESERVADAS (la carga del
    día no baja para terceros). Si el destino se aprueba, él carga las horas; si
    se rechaza/revoca, la cesión se anula (anulada_en) y todo vuelve atrás.
    """
    asignacion_origen = models.ForeignKey(
        Asignacion, on_delete=models.PROTECT, related_name="cesiones_realizadas",
    )
    asignacion_destino = models.ForeignKey(
        Asignacion, on_delete=models.PROTECT, related_name="cesiones_recibidas",
    )
    fecha = models.DateField(help_text="Día laborado cuyas horas se ceden.")
    horas = models.DecimalField(max_digits=4, decimal_places=1)
    politica = models.CharField(
        max_length=20, choices=Asignacion.POLITICA_CHOICES,
        help_text="RECOMPUTAR: la original extiende fecha_fin para recuperar las horas. "
                  "REDUCIR: la original baja su total de horas.",
    )
    # Tarifa vigente del recurso el día laborado: es la que se carga al proyecto
    # receptor y la que se descuenta del original (decisión de negocio 2026-07-09).
    tarifa_hora = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fecha_fin_original = models.DateField(
        null=True, blank=True,
        help_text="fecha_fin de la asignación origen antes de recomputar (para poder anular).",
    )
    creado_por = models.ForeignKey(User, on_delete=models.PROTECT, related_name="cesiones_creadas")
    creado_en = models.DateTimeField(auto_now_add=True)
    anulada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Cesión de horas"
        verbose_name_plural = "Cesiones de horas"
        ordering = ["-creado_en"]

    def __str__(self):
        return (
            f"{self.asignacion_origen.recurso} cede {self.horas} h del {self.fecha} "
            f"→ {self.asignacion_destino.proyecto}"
        )

    @property
    def activa(self):
        return self.anulada_en is None


class LogAuditoria(models.Model):
    """Registro append-only de cambios de estado en asignaciones. No editar ni borrar."""
    ACCION_CHOICES = [
        ("CREAR", "Crear"),
        ("APROBAR", "Aprobar"),
        ("RECHAZAR", "Rechazar"),
        ("REVOCAR", "Revocar"),
        ("INVALIDAR", "Invalidar"),
        ("CEDER", "Ceder horas"),
        ("ANULAR_CESION", "Anular cesión"),
        ("RECOMPUTO_TARIFA", "Recomputo por cambio de tarifa"),
    ]
    asignacion = models.ForeignKey(Asignacion, on_delete=models.PROTECT, related_name="log")
    accion = models.CharField(max_length=20, choices=ACCION_CHOICES)
    # actor nulo = acción automática del sistema (ej: recomputo por cambio de tarifa)
    actor = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    detalle = models.JSONField(default=dict)

    class Meta:
        ordering = ["timestamp"]
        verbose_name = "Log de Auditoría"
        verbose_name_plural = "Logs de Auditoría"

    def __str__(self):
        return f"{self.accion} — {self.asignacion} ({self.timestamp:%Y-%m-%d %H:%M})"
