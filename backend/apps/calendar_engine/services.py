import holidays
from datetime import date, timedelta
from functools import lru_cache


@lru_cache(maxsize=10)
def _feriados_colombia(year: int) -> frozenset:
    """Cache inmutable de feriados por año. Colombia respeta Ley Emiliani."""
    return frozenset(holidays.Colombia(years=year).keys())


def es_habil(fecha: date, recurso=None) -> bool:
    """
    Devuelve True si la fecha es un día hábil para el recurso dado.
    Orden de verificación: fin de semana → feriado Colombia → día no laborable global → indisponibilidad.
    """
    if fecha.weekday() >= 5:
        return False

    if fecha in _feriados_colombia(fecha.year):
        return False

    from .models import DiaNoLaborable, Indisponibilidad

    if DiaNoLaborable.objects.filter(fecha=fecha).exists():
        return False

    if recurso is not None:
        if Indisponibilidad.objects.filter(
            recurso=recurso,
            fecha_inicio__lte=fecha,
            fecha_fin__gte=fecha,
        ).exists():
            return False

    return True


def calcular_fecha_fin(fecha_inicio: date, dias_necesarios: int, recurso=None) -> date:
    """Avanza sobre días hábiles hasta completar dias_necesarios."""
    fecha = fecha_inicio
    habiles = 0
    while habiles < dias_necesarios:
        if es_habil(fecha, recurso):
            habiles += 1
            if habiles == dias_necesarios:
                break
        fecha += timedelta(days=1)
    return fecha


def feriados_en_rango(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Lista de feriados colombianos entre dos fechas, ordenados."""
    years = range(fecha_inicio.year, fecha_fin.year + 1)
    result = []
    for year in years:
        for fecha, nombre in holidays.Colombia(years=year).items():
            if fecha_inicio <= fecha <= fecha_fin:
                result.append({"fecha": fecha.isoformat(), "nombre": nombre})
    return sorted(result, key=lambda x: x["fecha"])
