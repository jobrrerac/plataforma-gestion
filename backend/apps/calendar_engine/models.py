from django.db import models
from django.contrib.auth.models import User
from apps.core.models import SoftDeleteModel, Recurso


class DiaNoLaborable(models.Model):
    """Día no laborable global (aplica a todos los recursos)."""
    fecha = models.DateField(unique=True)
    descripcion = models.CharField(max_length=200)
    creado_por = models.ForeignKey(User, on_delete=models.PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Día No Laborable"
        verbose_name_plural = "Días No Laborables"
        ordering = ["fecha"]

    def __str__(self):
        return f"{self.fecha} — {self.descripcion}"


class Indisponibilidad(SoftDeleteModel):
    """Período de no disponibilidad de un recurso específico.

    Ciclo de vida: el ingeniero registra su novedad (vacaciones o permiso) y
    queda PENDIENTE; un Admin la aprueba o la rechaza. **Solo las APROBADAS
    descuentan capacidad**: mientras está pendiente el recurso sigue contando
    como disponible, porque una solicitud que nadie ha revisado no puede
    bloquear la planificación (ver `calendar_engine.services`).

    Lo que crea un PM o un Admin directamente nace ya APROBADO: si lo registra
    una autoridad, no hay nada que aprobar. Lo mismo vale para lo que llegue
    sincronizado desde SAP.

    Cancelar una novedad pendiente es un soft-delete, como en el resto del
    proyecto: la fila se conserva para auditoría.
    """
    TIPO_CHOICES = [
        # El valor almacenado sigue siendo VACACION: cambiarlo obligaría a
        # migrar los datos y a tocar todo lo que filtre por él, sin ganar nada.
        # Lo que ve la gente es la etiqueta.
        ("VACACION", "Vacaciones"),
        ("PERMISO", "Permiso"),
    ]
    ORIGEN_CHOICES = [
        ("MANUAL", "Manual"),
        ("SAP", "SAP"),
    ]
    ESTADO_CHOICES = [
        ("PENDIENTE", "Pendiente de aprobación"),
        ("APROBADA", "Aprobada"),
        ("RECHAZADA", "Rechazada"),
    ]
    recurso = models.ForeignKey(Recurso, on_delete=models.CASCADE, related_name="indisponibilidades")
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    origen = models.CharField(max_length=10, choices=ORIGEN_CHOICES, default="MANUAL")
    external_id = models.CharField(max_length=100, null=True, blank=True)

    # El default es PENDIENTE a propósito: obliga a que quien crea la novedad
    # desde una vía con autoridad (PM, Admin, SAP) la apruebe explícitamente,
    # en vez de que una solicitud se cuele como aprobada por olvido.
    estado = models.CharField(
        max_length=20, choices=ESTADO_CHOICES, default="PENDIENTE",
        help_text="Solo las APROBADAS descuentan capacidad del recurso.",
    )
    motivo = models.CharField(
        max_length=200, blank=True,
        help_text="Nota de quien solicita la novedad (opcional).",
    )
    solicitada_por = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name="novedades_solicitadas",
        help_text="Vacío en las cargadas antes de este flujo o sincronizadas desde SAP.",
    )
    revisada_por = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name="novedades_revisadas",
        help_text="Admin que aprobó o rechazó la novedad.",
    )
    revisada_en = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Indisponibilidad"
        verbose_name_plural = "Indisponibilidades"
        ordering = ["fecha_inicio"]
        indexes = [
            # El panel del ingeniero y el del Admin filtran por estas dos
            # columnas en cada carga.
            models.Index(fields=["recurso", "estado"]),
        ]

    def __str__(self):
        return f"{self.recurso} — {self.get_tipo_display()} ({self.fecha_inicio} / {self.fecha_fin})"

    @property
    def dias_calendario(self):
        return (self.fecha_fin - self.fecha_inicio).days + 1

    @property
    def es_pendiente(self):
        return self.estado == "PENDIENTE"
