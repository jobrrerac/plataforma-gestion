"""Legalización de horas: qué hizo cada persona con su jornada.

Es el reverso de `assignments`. Allí un PM **solicita** un recurso para un
proyecto; aquí la persona **declara** en qué se le fue el día. Son dos hechos
distintos que pueden no coincidir, y esa discrepancia es justamente lo que se
quiere poder ver.
"""

from django.contrib.auth.models import User
from django.db import models

from apps.core.models import Proyecto, Recurso, SoftDeleteModel


class TipoActividad(models.Model):
    """A qué se dedicó una hora.

    Solo existen dos naturalezas:

    - **Proyecto** (`requiere_proyecto = True`): la hora se imputa a un
      `Proyecto` concreto, sea de cliente o interno. Los proyectos internos no
      son un concepto aparte: son proyectos marcados como no facturables, y
      deben estar dados de alta como cualquier otro. La clave foránea es lo que
      garantiza que «Departamentales» signifique siempre lo mismo, en vez de
      convertirse en cinco variantes escritas a mano.

    - **Todo lo demás** (`requiere_proyecto = False`): formación, estudio,
      entrenamiento. No cuelgan de ningún proyecto y no se pide nada más que el
      detalle de lo que se hizo.

    El catálogo es editable por Admin: si mañana aparece una actividad nueva, se
    añade una fila, no se toca el código.
    """

    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.CharField(
        max_length=300, blank=True,
        help_text=(
            "Cuándo usar esta actividad. Se muestra al elegirla, y es lo que "
            "evita que dos categorías parecidas se rellenen al azar."
        ),
    )
    requiere_proyecto = models.BooleanField(
        default=False,
        help_text="Si está marcado, al registrar horas hay que indicar a qué proyecto.",
    )
    activo = models.BooleanField(
        default=True,
        help_text="Desmarcar para retirarlo de los desplegables sin perder el histórico.",
    )
    orden = models.PositiveSmallIntegerField(
        default=100,
        help_text="Posición en el desplegable. Menor aparece primero.",
    )

    class Meta:
        verbose_name = "Tipo de actividad"
        verbose_name_plural = "Tipos de actividad"
        ordering = ["orden", "nombre"]

    def __str__(self):
        return self.nombre


class DiaLegalizado(SoftDeleteModel):
    """Un día de una persona, y su estado de cierre.

    La unidad de cierre es el **día**, no la semana: se registra, se muestra un
    resumen, se acepta, y a partir de ahí no se toca. Cambiar algo exige que
    alguien autorice la reapertura.

    Esa inmutabilidad es el punto de todo el módulo. Si las horas se pueden
    editar indefinidamente, el informe de facturables deja de significar nada:
    los números cambiarían después de haberlos reportado.

    Un día no hábil —fin de semana, feriado o ausencia aprobada— no necesita
    fila: no hay nada que legalizar. La ausencia ya la aprobó alguien en el
    panel de novedades y es la fuente de verdad; nadie teclea sus vacaciones
    dos veces.
    """

    ABIERTO = "ABIERTO"
    REGISTRADO = "REGISTRADO"
    APROBADO = "APROBADO"
    ESTADO_CHOICES = [
        (ABIERTO, "Abierto"),
        (REGISTRADO, "Registrado"),
        (APROBADO, "Aprobado"),
    ]

    recurso = models.ForeignKey(Recurso, on_delete=models.PROTECT, related_name="dias_legalizados")
    fecha = models.DateField()
    estado = models.CharField(max_length=12, choices=ESTADO_CHOICES, default=ABIERTO)

    # Se congela al registrar. Guardarlo evita recalcularlo en cada informe y,
    # sobre todo, deja constancia de con qué jornada se cerró el día.
    total_horas = models.DecimalField(max_digits=4, decimal_places=1, default=0)
    jornada_esperada = models.DecimalField(
        max_digits=4, decimal_places=1, default=0,
        help_text="Jornada que regía ese día. Se guarda por si la jornada legal cambia después.",
    )

    registrado_en = models.DateTimeField(null=True, blank=True)
    aprobado_por = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name="dias_aprobados",
    )
    aprobado_en = models.DateTimeField(null=True, blank=True)
    motivo_devolucion = models.CharField(
        max_length=300, blank=True,
        help_text=(
            "Por qué se devolvió el día para corregir. Lo ve quien lo registró: "
            "devolver sin decir qué está mal solo genera un segundo intento a ciegas."
        ),
    )

    class Meta:
        verbose_name = "Día legalizado"
        verbose_name_plural = "Días legalizados"
        ordering = ["-fecha"]
        constraints = [
            # Un día por persona. Sin esto, dos pestañas abiertas crearían dos
            # días para la misma fecha y las horas se contarían por duplicado.
            models.UniqueConstraint(
                fields=["recurso", "fecha"],
                condition=models.Q(deleted_at__isnull=True),
                name="un_dia_legalizado_por_recurso_y_fecha",
            ),
        ]
        indexes = [
            models.Index(fields=["recurso", "fecha"]),
            models.Index(fields=["estado", "fecha"]),
        ]

    def __str__(self):
        return f"{self.recurso} — {self.fecha} ({self.get_estado_display()})"

    @property
    def editable(self):
        return self.estado == self.ABIERTO

    @property
    def cuadra(self):
        return self.total_horas == self.jornada_esperada


class RegistroHoras(SoftDeleteModel):
    """Un renglón dentro de un día: qué se hizo y cuántas horas costó.

    Se puede partir el día en tantos renglones como haga falta, pero la suma
    tiene que dar la jornada exacta. Rellenar hasta cuadrar —aunque sea con
    estudio o con un proyecto interno— es lo que convierte esto en un registro
    fiable en vez de una aproximación.
    """

    dia = models.ForeignKey(DiaLegalizado, on_delete=models.CASCADE, related_name="registros")
    tipo_actividad = models.ForeignKey(TipoActividad, on_delete=models.PROTECT, related_name="registros")
    proyecto = models.ForeignKey(
        Proyecto, on_delete=models.PROTECT, null=True, blank=True, related_name="registros_horas",
        help_text="Obligatorio si el tipo de actividad lo exige; vacío en el resto.",
    )
    horas = models.DecimalField(max_digits=4, decimal_places=1)
    detalle = models.CharField(
        max_length=300,
        help_text="Qué se hizo. Es lo que permite legalizar estas horas después.",
    )

    class Meta:
        verbose_name = "Registro de horas"
        verbose_name_plural = "Registros de horas"
        ordering = ["id"]

    def __str__(self):
        destino = self.proyecto.codigo if self.proyecto_id else self.tipo_actividad.nombre
        return f"{destino} — {self.horas} h"

    @property
    def facturable(self):
        """Solo cuentan como facturables las horas de un proyecto de cliente."""
        return bool(self.proyecto_id and self.proyecto.facturable)
