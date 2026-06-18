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
    """Período de no disponibilidad de un recurso específico."""
    TIPO_CHOICES = [
        ("VACACION", "Vacación"),
        ("PERMISO", "Permiso"),
    ]
    ORIGEN_CHOICES = [
        ("MANUAL", "Manual"),
        ("SAP", "SAP"),
    ]
    recurso = models.ForeignKey(Recurso, on_delete=models.CASCADE, related_name="indisponibilidades")
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    origen = models.CharField(max_length=10, choices=ORIGEN_CHOICES, default="MANUAL")
    external_id = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        verbose_name = "Indisponibilidad"
        verbose_name_plural = "Indisponibilidades"
        ordering = ["fecha_inicio"]

    def __str__(self):
        return f"{self.recurso} — {self.get_tipo_display()} ({self.fecha_inicio} / {self.fecha_fin})"
