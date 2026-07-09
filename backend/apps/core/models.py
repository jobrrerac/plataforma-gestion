from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from .validators import validar_codigo_pep, validar_codigo_proyecto, validar_grafo


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


class Cluster(models.Model):
    """Unidad organizativa / pool al que pertenece un recurso en SAP."""
    codigo = models.CharField(max_length=20, unique=True, verbose_name="Código")
    nombre = models.CharField(max_length=100, blank=True, verbose_name="Descripción")

    class Meta:
        ordering = ["codigo"]
        verbose_name = "Cluster"
        verbose_name_plural = "Clusters"

    def __str__(self):
        return f"{self.codigo}" + (f" — {self.nombre}" if self.nombre else "")


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
    nro_persona_sap = models.CharField(
        max_length=20, unique=True, null=True, blank=True,
        verbose_name="N° persona SAP",
        help_text="Identificador único de persona en SAP (ej: 30011076).",
    )
    clusters = models.ManyToManyField(
        Cluster, blank=True, related_name="recursos", verbose_name="Clusters",
    )
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


def recursos_asignables():
    """
    Recursos que pueden recibir asignaciones (los que se muestran en el
    dashboard y en el buscador de solicitudes): activos y cuyo usuario de
    login NO es Admin, PM, staff ni superusuario. Los recursos sin usuario
    vinculado se consideran asignables.
    """
    from apps.accounts.roles import ADMIN, PM

    return (
        Recurso.objects.filter(activo=True)
        .exclude(usuario__is_staff=True)
        .exclude(usuario__is_superuser=True)
        .exclude(usuario__groups__name__in=[ADMIN, PM])
    )


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


class TarifaVigente(models.Model):
    """Historial de tarifas por hora de un recurso. Append-only: nunca se edita ni borra."""
    recurso = models.ForeignKey(
        Recurso, on_delete=models.PROTECT, related_name="tarifas",
    )
    valor_hora = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name="Tarifa €/h",
    )
    fecha_desde = models.DateField(
        verbose_name="Vigente desde",
        help_text="Fecha a partir de la cual aplica esta tarifa.",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_desde"]
        unique_together = [("recurso", "fecha_desde")]
        verbose_name = "Tarifa"
        verbose_name_plural = "Tarifas"

    def __str__(self):
        return f"{self.recurso.nombre} — {self.valor_hora} €/h (desde {self.fecha_desde})"

    @classmethod
    def vigente_para(cls, recurso, fecha=None):
        """Retorna la tarifa activa en la fecha dada (la más reciente con fecha_desde ≤ fecha)."""
        from datetime import date as _date
        if fecha is None:
            fecha = _date.today()
        return (
            cls.objects
            .filter(recurso=recurso, fecha_desde__lte=fecha)
            .order_by("-fecha_desde")
            .first()
        )


class Proyecto(SoftDeleteModel):
    ESTADO_CHOICES = [
        ("ACTIVO", "Activo"),
        ("EN_PAUSA", "En Pausa"),
        ("CERRADO", "Cerrado"),
    ]
    codigo = models.CharField(
        max_length=50, unique=True,
        validators=[validar_codigo_proyecto],
        help_text="Código del proyecto en SAP (ej: V-00869252/D).",
    )
    codigo_pep = models.CharField(
        max_length=50, unique=True, null=True, blank=True,
        verbose_name="Código PEP",
        validators=[validar_codigo_pep],
        help_text="Elemento PEP del proyecto en SAP (ej: L-00869252/A). Único cuando se informa.",
    )
    # Jerarquía SAP plana (relación 1:1:1): Proyecto (codigo) → PEP → Grafo
    grafo = models.CharField(
        max_length=50, unique=True, null=True, blank=True,
        verbose_name="Grafo",
        validators=[validar_grafo],
        help_text="Grafo (orden de red) del proyecto en SAP (ej: 2000269630). Único cuando se informa.",
    )
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
