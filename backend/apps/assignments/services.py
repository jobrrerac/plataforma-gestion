from math import ceil
from datetime import timedelta
from django.db import transaction
from apps.calendar_engine.services import calcular_fecha_fin as _cal_fecha_fin, es_habil
from .models import Asignacion, LogAuditoria


def calcular_fecha_fin(recurso, fecha_inicio, horas_totales, intensidad_diaria):
    """Wrapper que convierte horas/intensidad a días y delega al motor de calendario."""
    dias = ceil(float(horas_totales) / float(intensidad_diaria))
    return _cal_fecha_fin(fecha_inicio, dias, recurso)


def carga_en_fecha(recurso, fecha, excluir_id=None) -> float:
    """Suma de intensidad_diaria de asignaciones APROBADAS del recurso en esa fecha."""
    qs = Asignacion.objects.filter(
        recurso=recurso,
        estado="APROBADA",
        fecha_inicio__lte=fecha,
        fecha_fin__gte=fecha,
    )
    if excluir_id:
        qs = qs.exclude(pk=excluir_id)
    return sum(float(a.intensidad_diaria) for a in qs)


def puede_asignar(asignacion) -> tuple[bool, object]:
    """
    Verifica que en ningún día hábil del rango la carga supere 8 h.
    Retorna (True, None) si cabe, (False, fecha_conflicto) si hay sobreasignación.
    """
    fecha = asignacion.fecha_inicio
    while fecha <= asignacion.fecha_fin:
        if es_habil(fecha, asignacion.recurso):
            carga = carga_en_fecha(asignacion.recurso, fecha, excluir_id=asignacion.pk)
            if carga + float(asignacion.intensidad_diaria) > 8:
                return False, fecha
        fecha += timedelta(days=1)
    return True, None


def aprobar_asignacion(asignacion, actor):
    """
    Aprobación transaccional con select_for_update por recurso.
    Lanza ValueError si hay sobreasignación.
    """
    with transaction.atomic():
        # Lock sobre el recurso para serializar aprobaciones concurrentes
        recurso = asignacion.recurso.__class__.all_objects.select_for_update().get(
            pk=asignacion.recurso_id
        )
        ok, fecha_conflicto = puede_asignar(asignacion)
        if not ok:
            raise ValueError(
                f"Sobreasignación: {recurso.nombre} ya tiene 8 h asignadas el {fecha_conflicto}."
            )
        asignacion.estado = "APROBADA"
        asignacion.save(update_fields=["estado", "updated_at"])
        LogAuditoria.objects.create(
            asignacion=asignacion,
            accion="APROBAR",
            actor=actor,
            detalle={"recurso_id": recurso.pk, "fecha_fin": str(asignacion.fecha_fin)},
        )


def rechazar_asignacion(asignacion, actor, motivo=""):
    asignacion.estado = "RECHAZADA"
    asignacion.save(update_fields=["estado", "updated_at"])
    LogAuditoria.objects.create(
        asignacion=asignacion, accion="RECHAZAR", actor=actor, detalle={"motivo": motivo}
    )


def revocar_asignacion(asignacion, actor, motivo=""):
    asignacion.estado = "REVOCADA"
    asignacion.save(update_fields=["estado", "updated_at"])
    LogAuditoria.objects.create(
        asignacion=asignacion, accion="REVOCAR", actor=actor, detalle={"motivo": motivo}
    )
