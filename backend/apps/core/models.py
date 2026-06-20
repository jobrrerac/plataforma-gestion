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
    descripcion = models.CharField(
        max_length=300, blank=True,
        help_text="Qué capacidades aporta este skill (máx. 300 caracteres).",
    )

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
    skills = models.ManyToManyField(
        Skill, through="RecursoSkill", blank=True, related_name="recursos"
    )
    usuario = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="recurso"
    )

    class Meta:
        verbose_name = "Recurso"
        verbose_name_plural = "Recursos"
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.nombre} ({self.get_banda_display()})"


class RecursoSkill(models.Model):
    """Relación Recurso↔Skill con nivel de dominio (suficiencia)."""
    SUFICIENCIA_CHOICES = [
        (1, "★ Básico"),
        (2, "★★ Elemental"),
        (3, "★★★ Intermedio"),
        (4, "★★★★ Avanzado"),
        (5, "★★★★★ Experto - Certificado"),
    ]
    recurso = models.ForeignKey(
        Recurso, on_delete=models.CASCADE, related_name="recurso_skills"
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="recurso_skills"
    )
    suficiencia = models.PositiveSmallIntegerField(
        choices=SUFICIENCIA_CHOICES, default=3,
        help_text="Nivel de dominio: 1 básico → 5 experto.",
    )

    class Meta:
        unique_together = [("recurso", "skill")]
        verbose_name = "Skill de recurso"
        verbose_name_plural = "Skills de recurso"
        ordering = ["-suficiencia", "skill__nombre"]

    def __str__(self):
        return f"{self.recurso.nombre} — {self.skill.nombre} ({'★' * self.suficiencia})"

    @property
    def estrellas(self):
        return "★" * self.suficiencia + "☆" * (5 - self.suficiencia)


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
