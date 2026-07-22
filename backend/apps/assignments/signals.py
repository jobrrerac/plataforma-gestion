"""
La tarifa se maneja de acuerdo con cambios en el costo del recurso: cuando se
registra una nueva vigencia (TarifaVigente es append-only), el costo estimado
de las asignaciones activas afectadas se recomputa automáticamente con el
cálculo mixto por día, y cada recomputo queda trazado en LogAuditoria con
acción RECOMPUTO_TARIFA (actor nulo = sistema).
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import TarifaVigente


@receiver(post_save, sender=TarifaVigente, dispatch_uid="recomputo_costos_tarifa")
def recomputar_costos_por_cambio_tarifa(sender, instance, created, **kwargs):
    if not created:
        return
    from .models import Asignacion, LogAuditoria
    from .services import costo_estimado_asignacion

    afectadas = Asignacion.objects.filter(
        recurso_id=instance.recurso_id,
        estado__in=["SOLICITADA", "APROBADA"],
        fecha_fin__gte=instance.fecha_desde,
    )
    for asignacion in afectadas:
        costo_anterior = asignacion.costo_estimado
        costo_nuevo = costo_estimado_asignacion(asignacion)
        if costo_nuevo == costo_anterior:
            continue
        asignacion.costo_estimado = costo_nuevo
        asignacion.save(update_fields=["costo_estimado", "updated_at"])
        LogAuditoria.objects.create(
            asignacion=asignacion,
            accion="RECOMPUTO_TARIFA",
            actor=None,  # acción automática del sistema
            detalle={
                "tarifa_nueva": float(instance.valor_hora),
                "tarifa_desde": instance.fecha_desde.isoformat(),
                "costo_antes": float(costo_anterior) if costo_anterior is not None else None,
                "costo_despues": float(costo_nuevo) if costo_nuevo is not None else None,
            },
        )
