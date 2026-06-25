from math import ceil
from decimal import Decimal
from datetime import date, timedelta
from django.db import transaction
from apps.calendar_engine.services import calcular_fecha_fin as _cal_fecha_fin, contar_dias_habiles, es_habil
from .models import Asignacion, LogAuditoria
from apps.core.models import TarifaVigente

# Jornada real: lun–jue 8.5 h, vie 8 h → máximo semanal 42 h
JORNADA_LUNES_JUEVES = 8.5
JORNADA_VIERNES = 8.0


def capacidad_maxima_dia(fecha: date) -> float:
    """Retorna la jornada máxima permitida según el día de la semana."""
    if fecha.weekday() == 4:  # viernes
        return JORNADA_VIERNES
    return JORNADA_LUNES_JUEVES  # lunes (0) a jueves (3)


def calcular_fecha_fin(recurso, fecha_inicio, horas_totales, intensidad_diaria):
    """Wrapper que convierte horas/intensidad a días y delega al motor de calendario."""
    dias = ceil(float(horas_totales) / float(intensidad_diaria))
    return _cal_fecha_fin(fecha_inicio, dias, recurso)


def calcular_horas_jornada_completa(fecha_inicio: date, fecha_fin: date, recurso=None) -> int:
    """Suma las horas máximas disponibles por cada día hábil del rango."""
    total = 0.0
    fecha = fecha_inicio
    while fecha <= fecha_fin:
        if es_habil(fecha, recurso):
            total += capacidad_maxima_dia(fecha)
        fecha += timedelta(days=1)
    return ceil(total)


def _carga_propia(asignacion, fecha: date) -> float:
    """Carga real de una asignación en un día: jornada completa usa el tope del día."""
    if asignacion.jornada_completa:
        return capacidad_maxima_dia(fecha)
    return float(asignacion.intensidad_diaria)


def carga_en_fecha(recurso, fecha, excluir_id=None) -> float:
    """Suma de carga de asignaciones APROBADAS del recurso en esa fecha."""
    qs = Asignacion.objects.filter(
        recurso=recurso,
        estado="APROBADA",
        fecha_inicio__lte=fecha,
        fecha_fin__gte=fecha,
    )
    if excluir_id:
        qs = qs.exclude(pk=excluir_id)
    return sum(_carga_propia(a, fecha) for a in qs)


def puede_asignar(asignacion) -> tuple[bool, object]:
    """
    Verifica que en ningún día hábil del rango la carga no supere la jornada del día:
      lun–jue → 8.5 h, vie → 8 h (máx 42 h semanales).
    Retorna (True, None) si cabe, (False, fecha_conflicto) si hay sobreasignación.
    """
    fecha = asignacion.fecha_inicio
    while fecha <= asignacion.fecha_fin:
        if es_habil(fecha, asignacion.recurso):
            carga = carga_en_fecha(asignacion.recurso, fecha, excluir_id=asignacion.pk)
            if carga + _carga_propia(asignacion, fecha) > capacidad_maxima_dia(fecha):
                return False, fecha
        fecha += timedelta(days=1)
    return True, None


def aprobar_asignacion(asignacion, actor):
    """
    Aprobación transaccional con select_for_update por recurso.
    Lanza ValueError si hay sobreasignación.
    """
    with transaction.atomic():
        recurso = asignacion.recurso.__class__.all_objects.select_for_update().get(
            pk=asignacion.recurso_id
        )
        ok, fecha_conflicto = puede_asignar(asignacion)
        if not ok:
            cap = capacidad_maxima_dia(fecha_conflicto)
            raise ValueError(
                f"Sobreasignación: {recurso.nombre} ya alcanza las {cap} h del {fecha_conflicto.strftime('%A %d/%m/%Y')}."
            )
        asignacion.estado = "APROBADA"
        asignacion.save(update_fields=["estado", "updated_at"])
        LogAuditoria.objects.create(
            asignacion=asignacion,
            accion="APROBAR",
            actor=actor,
            detalle={"recurso_id": recurso.pk, "fecha_fin": str(asignacion.fecha_fin)},
        )


def detalle_dias_recurso(recurso, fecha_inicio: date, fecha_fin: date) -> list:
    """Detalle de ocupación por día hábil para un único recurso."""
    result = []
    fecha = fecha_inicio
    while fecha <= fecha_fin:
        if es_habil(fecha, recurso):
            cap = capacidad_maxima_dia(fecha)
            carga = carga_en_fecha(recurso, fecha)
            libre = max(0.0, cap - carga)
            pct_ocu = round(100.0 * min(carga, cap) / cap, 1) if cap > 0 else 0.0
            result.append({
                "fecha": fecha,
                "horas_cap": cap,
                "horas_ocupadas": round(min(carga, cap), 1),
                "horas_libres": round(libre, 1),
                "pct_ocupado": pct_ocu,
                "lleno": carga >= cap,
                "parcial": 0 < carga < cap,
            })
        fecha += timedelta(days=1)
    return result


def disponibilidad_recursos(fecha_inicio: date, fecha_fin: date, skills: list | None = None) -> list:
    """
    Devuelve la disponibilidad de cada recurso activo en el rango dado.
    Filtra por skills si se pasa una lista de nombres.
    Ordena de más a menos disponible.
    """
    from apps.core.models import Recurso

    qs = Recurso.objects.prefetch_related("recurso_skills__skill").filter(activo=True).order_by("nombre")
    if skills:
        qs = qs.filter(skills__nombre__in=skills).distinct()

    resultados = []
    for recurso in qs:
        horas_cap = 0.0
        horas_ocupadas = 0.0
        dias_habiles = 0
        dias_sin_cupo = 0
        dias_con_carga = []

        fecha = fecha_inicio
        while fecha <= fecha_fin:
            if es_habil(fecha, recurso):
                cap = capacidad_maxima_dia(fecha)
                carga = carga_en_fecha(recurso, fecha)
                libre = max(0.0, cap - carga)
                pct_ocu = round(100.0 * min(carga, cap) / cap, 1) if cap > 0 else 0.0
                horas_cap += cap
                horas_ocupadas += min(carga, cap)
                dias_habiles += 1
                if carga >= cap:
                    dias_sin_cupo += 1
                if carga > 0:
                    dias_con_carga.append({
                        "fecha": fecha,
                        "horas_ocupadas": round(min(carga, cap), 1),
                        "horas_libres": round(libre, 1),
                        "horas_cap": cap,
                        "pct_ocupado": pct_ocu,
                        "lleno": carga >= cap,
                    })
            fecha += timedelta(days=1)

        horas_libres = max(0.0, horas_cap - horas_ocupadas)
        pct_libre = round(100.0 * horas_libres / horas_cap, 1) if horas_cap > 0 else 0.0

        tarifa_obj = TarifaVigente.vigente_para(recurso, fecha_inicio)
        tarifa_hora = float(tarifa_obj.valor_hora) if tarifa_obj else None
        costo_estimado = round(tarifa_hora * horas_libres, 2) if tarifa_hora and horas_libres else None

        resultados.append({
            "recurso": recurso,
            "skills": [
                {
                    "nombre": rs.skill.nombre,
                    "suficiencia": rs.suficiencia,
                    "estrellas": "★" * rs.suficiencia + "☆" * (5 - rs.suficiencia),
                }
                for rs in recurso.recurso_skills.all()
            ],
            "dias_habiles": dias_habiles,
            "horas_capacidad": round(horas_cap, 1),
            "horas_ocupadas": round(horas_ocupadas, 1),
            "horas_libres": round(horas_libres, 1),
            "porcentaje_libre": pct_libre,
            "porcentaje_ocupado": round(100.0 - pct_libre, 1),
            "dias_sin_cupo": dias_sin_cupo,
            "dias_con_carga": dias_con_carga,
            "tarifa_hora": tarifa_hora,
            "costo_estimado": costo_estimado,
        })

    resultados.sort(key=lambda x: x["porcentaje_libre"], reverse=True)
    return resultados


def calcular_solicitud_horas(recurso, fecha_inicio: date, horas_target: float, intensidad: float | None = None, jornada_completa: bool = False) -> tuple:
    """
    Calcula (fecha_fin, dias_habiles, horas_reales, dias_bloqueados) para una solicitud
    por horas totales, saltando días donde la carga existente + intensidad excede la capacidad.
    A diferencia de calcular_fecha_fin(), respeta la ocupación real del recurso.
    """
    acum = 0.0
    dias_count = 0
    dias_bloqueados = []
    fecha = fecha_inicio
    limite = fecha_inicio + timedelta(days=730)

    while acum < horas_target and fecha <= limite:
        if es_habil(fecha, recurso):
            cap = capacidad_maxima_dia(fecha)
            carga_existente = carga_en_fecha(recurso, fecha)
            h_dia = cap if jornada_completa else intensidad

            if carga_existente + h_dia > cap:
                dias_bloqueados.append(fecha)
            else:
                h_hoy = min(h_dia, horas_target - acum)
                acum += h_hoy
                dias_count += 1
                if acum >= horas_target:
                    return fecha, dias_count, int(ceil(acum)), dias_bloqueados
        fecha += timedelta(days=1)

    return fecha, dias_count, int(ceil(acum)), dias_bloqueados


def crear_solicitud_por_horas(recurso, proyecto, fecha_inicio, horas_target, intensidad, jornada_completa, solicitante):
    """Crea una Asignacion SOLICITADA en modo HORAS calculando fecha_fin respetando ocupación existente."""
    ff, dias, horas_reales, dias_bloqueados = calcular_solicitud_horas(
        recurso, fecha_inicio, float(horas_target), intensidad, jornada_completa
    )
    intens_dec = Decimal("8.0") if jornada_completa else Decimal(str(intensidad))
    asignacion = Asignacion.objects.create(
        recurso=recurso,
        proyecto=proyecto,
        modo_asignacion="HORAS",
        fecha_inicio=fecha_inicio,
        fecha_fin=ff,
        dias_habiles=dias,
        horas_totales=int(horas_target),
        intensidad_diaria=intens_dec,
        jornada_completa=jornada_completa,
        estado="SOLICITADA",
        solicitada_por=solicitante,
    )
    LogAuditoria.objects.create(
        asignacion=asignacion, accion="CREAR", actor=solicitante,
        detalle={"modo": "HORAS_FILL", "horas_target": int(horas_target), "dias": dias, "bloqueados": len(dias_bloqueados)},
    )
    return asignacion, dias_bloqueados


def analizar_conflictos(asignacion):
    """
    Detecta días con sobreasignación y calcula la nueva fecha_fin si se recomputa saltándolos.
    Retorna (conflict_dates: list[date], nueva_fecha_fin: date|None, nuevas_horas: int|None).
    """
    conflict_dates = []
    conflict_set = set()
    fecha = asignacion.fecha_inicio
    while fecha <= asignacion.fecha_fin:
        if es_habil(fecha, asignacion.recurso):
            carga = carga_en_fecha(asignacion.recurso, fecha, excluir_id=asignacion.pk)
            if carga + _carga_propia(asignacion, fecha) > capacidad_maxima_dia(fecha):
                conflict_dates.append(fecha)
                conflict_set.add(fecha)
        fecha += timedelta(days=1)

    if not conflict_dates:
        return [], None, None

    needed = asignacion.dias_habiles or contar_dias_habiles(
        asignacion.fecha_inicio, asignacion.fecha_fin, asignacion.recurso
    )

    count = 0
    total_horas = 0.0
    nueva_fecha_fin = asignacion.fecha_inicio
    fecha = asignacion.fecha_inicio
    limite = asignacion.fecha_inicio + timedelta(days=730)

    while count < needed and fecha <= limite:
        if es_habil(fecha, asignacion.recurso) and fecha not in conflict_set:
            count += 1
            total_horas += (
                capacidad_maxima_dia(fecha) if asignacion.jornada_completa
                else float(asignacion.intensidad_diaria or 8.0)
            )
            nueva_fecha_fin = fecha
        fecha += timedelta(days=1)

    nuevas_horas = ceil(total_horas) if asignacion.jornada_completa else asignacion.horas_totales
    return conflict_dates, nueva_fecha_fin, nuevas_horas


def aprobar_recomputando(asignacion, actor, nueva_fecha_fin, nuevas_horas):
    """Aprueba extendiendo fecha_fin para saltar los días conflictivos."""
    with transaction.atomic():
        recurso = asignacion.recurso.__class__.all_objects.select_for_update().get(
            pk=asignacion.recurso_id
        )
        asignacion.fecha_fin = nueva_fecha_fin
        if nuevas_horas is not None:
            asignacion.horas_totales = nuevas_horas
        ok, fecha_conflicto = puede_asignar(asignacion)
        if not ok:
            raise ValueError(
                f"Sigue habiendo conflicto el {fecha_conflicto.strftime('%d/%m/%Y')} tras recomputar."
            )
        asignacion.estado = "APROBADA"
        asignacion.save(update_fields=["estado", "fecha_fin", "horas_totales", "updated_at"])
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="APROBAR", actor=actor,
            detalle={
                "recurso_id": recurso.pk,
                "fecha_fin_recomputada": str(nueva_fecha_fin),
                "recomputo": True,
            },
        )


def crear_solicitud(recurso, proyecto, fecha_inicio, fecha_fin, intensidad_diaria, jornada_completa, solicitante):
    """Crea una Asignacion SOLICITADA en modo RANGO desde el flujo de solicitud de recursos."""
    dias = contar_dias_habiles(fecha_inicio, fecha_fin, recurso)
    if jornada_completa:
        intensidad = Decimal("8.0")
        horas = calcular_horas_jornada_completa(fecha_inicio, fecha_fin, recurso)
    else:
        intensidad = Decimal(str(intensidad_diaria))
        horas = ceil(dias * float(intensidad))
    asignacion = Asignacion.objects.create(
        recurso=recurso,
        proyecto=proyecto,
        modo_asignacion="RANGO",
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        dias_habiles=dias,
        horas_totales=horas,
        intensidad_diaria=intensidad,
        jornada_completa=jornada_completa,
        estado="SOLICITADA",
        solicitada_por=solicitante,
    )
    LogAuditoria.objects.create(
        asignacion=asignacion, accion="CREAR", actor=solicitante,
        detalle={"modo": "RANGO", "dias_habiles": dias, "horas_totales": horas},
    )
    return asignacion


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
