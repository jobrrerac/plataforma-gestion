from django.conf import settings
from django.db import models


class CambioPasswordPendiente(models.Model):
    """
    Marca que un usuario debe cambiar su contraseña antes de seguir usando la
    plataforma (p. ej. tras crearle una credencial temporal). La sola existencia
    de la fila = cambio obligatorio; se borra en cuanto el usuario la cambia.

    No es una entidad de negocio soft-delete: es estado transitorio de la cuenta,
    por eso aquí sí aplica el borrado físico al completarse el cambio.
    """

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cambio_password_pendiente",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(
        max_length=200,
        blank=True,
        default="Credencial temporal: debe cambiarse en el primer inicio de sesión.",
    )

    class Meta:
        verbose_name = "Cambio de contraseña pendiente"
        verbose_name_plural = "Cambios de contraseña pendientes"

    def __str__(self):
        return f"Cambio pendiente: {self.usuario.get_username()}"
