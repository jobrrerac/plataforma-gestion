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


class AppendOnlyError(Exception):
    """Se intento modificar o borrar un registro que no admite cambios."""


class AppendOnlyQuerySet(models.QuerySet):
    """Cierra tambien las puertas de conjunto.

    `Model.save()` y `Model.delete()` no se enteran de un
    `Modelo.objects.filter(...).update(...)`: eso baja directo a SQL. Sin
    bloquearlo aqui, la proteccion del modelo seria una valla con la verja
    abierta al lado.
    """

    def update(self, **kwargs):
        raise AppendOnlyError(
            f"{self.model.__name__} es append-only: no se puede actualizar en bloque. "
            "Para corregir un dato, registra una entrada nueva."
        )

    def delete(self):
        raise AppendOnlyError(
            f"{self.model.__name__} es append-only: no se puede borrar en bloque."
        )


class AppendOnlyModel(models.Model):
    """Registro que solo se crea: ni se edita ni se borra.

    La regla estaba escrita en los docstrings y aplicada unicamente en el admin.
    Eso deja fuera el shell de produccion, un script de migracion, una
    integracion futura o simplemente el proximo que escriba
    `.objects.update(...)` sin saber que no debia.

    Un rastro de auditoria que se puede reescribir no es un rastro de auditoria,
    y una tarifa historica que se puede editar cambia costos ya reportados. Por
    eso ademas de esto hay un disparador en PostgreSQL: es lo unico que sigue en
    pie cuando el codigo se salta el ORM.
    """

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        # `_state.adding` es False en cuanto la fila existe, tanto si se cargo
        # de la base como si se acaba de crear. Comprobar `pk is not None` no
        # basta: una instancia nueva con pk explicito tambien lo tendria.
        if not self._state.adding:
            raise AppendOnlyError(
                f"{type(self).__name__} es append-only: no se puede modificar una entrada "
                "ya registrada. Para corregir un dato, registra una entrada nueva."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError(
            f"{type(self).__name__} es append-only: no se puede borrar una entrada registrada."
        )


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


class TarifaVigente(AppendOnlyModel):
    """Historial de tarifas por hora de un recurso.

    Append-only de verdad, no solo de palabra: la inmutabilidad la imponen el
    modelo y un disparador de PostgreSQL, no el admin. Editar una tarifa ya
    registrada cambiaria costos historicos ya reportados, y ademas no dispara
    el recomputo (`signals.py` solo reacciona al alta), asi que las asignaciones
    se quedarian con el costo viejo sin que nada avisara.

    Una correccion se registra como una vigencia nueva.
    """
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
    aprobador_delegado = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name="proyectos_aprobador",
        verbose_name="Aprobador delegado",
        help_text=(
            "Quien puede aprobar las horas de este proyecto ademas del PM. "
            "Puede ser cualquiera —un ingeniero, un admin, otro PM—: la "
            "delegacion ES la autorizacion, no hace falta que tenga un rol "
            "concreto. No da acceso a costos ni a nada mas del proyecto."
        ),
    )
    facturable = models.BooleanField(
        default=True,
        help_text=(
            "Las horas de este proyecto se cobran a un cliente. Desmarcar en los "
            "proyectos internos (formación interna, gestión departamental...): "
            "siguen siendo proyectos normales y hay que darlos de alta igual, "
            "pero no entran en el informe de horas facturables."
        ),
    )

    class Meta:
        verbose_name = "Proyecto"
        verbose_name_plural = "Proyectos"
        ordering = ["-fecha_inicio"]

    def __str__(self):
        return f"{self.codigo} — {self.nombre}"
