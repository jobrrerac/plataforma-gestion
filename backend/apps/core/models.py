from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class SoftDeleteModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    def delete(self, using=None, keep_parents=False):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    class Meta:
        abstract = True


class Skill(models.Model):
    """Skills técnicos. En producción se sincronizarán desde el sistema de Skills vía adaptador."""
    nombre = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Skill"
        verbose_name_plural = "Skills"

    def __str__(self):
        return self.nombre


class Recurso(SoftDeleteModel):
    BANDA_CHOICES = [
        ("JR", "Junior"),
        ("SSR", "Semi-Senior"),
        ("SR", "Senior"),
        ("LEAD", "Tech Lead"),
    ]
    nombre = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    banda = models.CharField(max_length=10, choices=BANDA_CHOICES)
    activo = models.BooleanField(default=True)
    skills = models.ManyToManyField(Skill, blank=True, related_name="recursos")
    usuario = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="recurso"
    )

    class Meta:
        verbose_name = "Recurso"
        verbose_name_plural = "Recursos"
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.get_banda_display()})"


class Proyecto(SoftDeleteModel):
    ESTADO_CHOICES = [
        ("ACTIVO", "Activo"),
        ("EN_PAUSA", "En Pausa"),
        ("CERRADO", "Cerrado"),
    ]
    codigo = models.CharField(max_length=50, unique=True)
    nombre = models.CharField(max_length=200)
    cliente = models.CharField(max_length=200)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="ACTIVO")
    pm = models.ForeignKey(User, on_delete=models.PROTECT, related_name="proyectos_pm")

    class Meta:
        verbose_name = "Proyecto"
        verbose_name_plural = "Proyectos"
        ordering = ["-fecha_inicio"]

    def __str__(self):
        return f"{self.codigo} — {self.nombre}"
