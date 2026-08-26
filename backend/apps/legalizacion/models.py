"""Legalización de horas: qué hizo cada persona con su jornada.

Es el reverso de `assignments`. Allí un PM **solicita** un recurso para un
proyecto; aquí la persona **declara** en qué se le fue el día. Son dos hechos
distintos que pueden no coincidir, y esa discrepancia es justamente lo que se
quiere poder ver.

Fase 1: solo los cimientos — el catálogo de actividades. El registro diario y
su cierre llegan después.
"""

from django.db import models


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
