import holidays
from datetime import date, timedelta
from functools import lru_cache


@lru_cache(maxsize=10)
def _feriados_colombia(year: int) -> frozenset:
    """Cache inmutable de feriados por año. Colombia respeta Ley Emiliani."""
    return frozenset(holidays.Colombia(years=year).keys())


class CalendarioRango:
    """
    Precarga en una sola query los días no laborables globales y las
    indisponibilidades de un rango, para evaluar es_habil() en memoria.
    Evita 1-2 queries por día dentro de bucles (dashboard, disponibilidad).

    fecha_fin=None precarga sin cota superior (para bucles cuyo fin no se
    conoce de antemano, como calcular_fecha_fin).
    recursos=None precarga las indisponibilidades de todos los recursos.
    """

    def __init__(self, fecha_inicio: date, fecha_fin: date | None = None, recursos=None):
        from .models import DiaNoLaborable, Indisponibilidad

        dnl = DiaNoLaborable.objects.filter(fecha__gte=fecha_inicio)
        # Solo las APROBADAS descuentan capacidad. Una novedad que nadie ha
        # revisado todavía no puede bloquear la planificación: el recurso sigue
        # contando como disponible hasta que un Admin la apruebe.
        indisp = Indisponibilidad.objects.filter(
            fecha_fin__gte=fecha_inicio, estado="APROBADA",
        )
        if fecha_fin is not None:
            dnl = dnl.filter(fecha__lte=fecha_fin)
            indisp = indisp.filter(fecha_inicio__lte=fecha_fin)
        if recursos is not None:
            ids = [r.pk if hasattr(r, "pk") else r for r in recursos]
            indisp = indisp.filter(recurso_id__in=ids)

        self.no_laborables = set(dnl.values_list("fecha", flat=True))
        self._indisp: dict[int, list[tuple[date, date, str]]] = {}
        for rec_id, ini, fin, tipo in indisp.values_list(
            "recurso_id", "fecha_inicio", "fecha_fin", "tipo"
        ):
            self._indisp.setdefault(rec_id, []).append((ini, fin, tipo))

    def es_habil(self, fecha: date, recurso=None) -> bool:
        return self.motivo_no_habil(fecha, recurso) is None

    def motivo_no_habil(self, fecha: date, recurso=None):
        """Por qué el día no es hábil, o None si sí lo es.

        Distinguir el motivo importa para el dashboard: un fin de semana o un
        feriado es un día en el que nadie trabaja, mientras que una ausencia
        (vacaciones o permiso aprobado) es una persona concreta no disponible.
        Pintarlos igual haría que un recurso de vacaciones se leyera como
        "libre", que es justo lo contrario.

        Valores: FINDE, FERIADO, NO_LABORABLE, AUSENCIA.
        """
        if fecha.weekday() >= 5:
            return "FINDE"
        if fecha in _feriados_colombia(fecha.year):
            return "FERIADO"
        if fecha in self.no_laborables:
            return "NO_LABORABLE"
        if recurso is not None:
            rec_id = recurso.pk if hasattr(recurso, "pk") else recurso
            for ini, fin, _tipo in self._indisp.get(rec_id, ()):
                if ini <= fecha <= fin:
                    return "AUSENCIA"
        return None

    def tipo_ausencia(self, fecha: date, recurso=None):
        """VACACION o PERMISO si ese día el recurso está ausente; None si no."""
        if recurso is None:
            return None
        rec_id = recurso.pk if hasattr(recurso, "pk") else recurso
        for ini, fin, tipo in self._indisp.get(rec_id, ()):
            if ini <= fecha <= fin:
                return tipo
        return None


def es_habil(fecha: date, recurso=None) -> bool:
    """
    Devuelve True si la fecha es un día hábil para el recurso dado.
    Orden de verificación: fin de semana → feriado Colombia → día no laborable
    global → indisponibilidad APROBADA (las pendientes no cuentan).

    Consulta la BD en cada llamada: para evaluar muchos días en bucle,
    usar CalendarioRango.
    """
    if fecha.weekday() >= 5:
        return False

    if fecha in _feriados_colombia(fecha.year):
        return False

    from .models import DiaNoLaborable, Indisponibilidad

    if DiaNoLaborable.objects.filter(fecha=fecha).exists():
        return False

    if recurso is not None:
        # Igual que en CalendarioRango: solo las APROBADAS cuentan.
        if Indisponibilidad.objects.filter(
            recurso=recurso,
            fecha_inicio__lte=fecha,
            fecha_fin__gte=fecha,
            estado="APROBADA",
        ).exists():
            return False

    return True


def calcular_fecha_fin(fecha_inicio: date, dias_necesarios: int, recurso=None) -> date:
    """Avanza sobre días hábiles hasta completar dias_necesarios."""
    cal = CalendarioRango(fecha_inicio, None, [recurso] if recurso is not None else None)
    fecha = fecha_inicio
    habiles = 0
    while habiles < dias_necesarios:
        if cal.es_habil(fecha, recurso):
            habiles += 1
            if habiles == dias_necesarios:
                break
        fecha += timedelta(days=1)
    return fecha


def contar_dias_habiles(fecha_inicio: date, fecha_fin: date, recurso=None) -> int:
    """Cuenta los días hábiles entre dos fechas (ambas inclusive)."""
    cal = CalendarioRango(fecha_inicio, fecha_fin, [recurso] if recurso is not None else None)
    count = 0
    fecha = fecha_inicio
    while fecha <= fecha_fin:
        if cal.es_habil(fecha, recurso):
            count += 1
        fecha += timedelta(days=1)
    return count


def feriados_en_rango(fecha_inicio: date, fecha_fin: date) -> list[dict]:
    """Lista de feriados colombianos entre dos fechas, ordenados."""
    years = range(fecha_inicio.year, fecha_fin.year + 1)
    result = []
    for year in years:
        for fecha, nombre in holidays.Colombia(years=year).items():
            if fecha_inicio <= fecha <= fecha_fin:
                result.append({"fecha": fecha.isoformat(), "nombre": nombre})
    return sorted(result, key=lambda x: x["fecha"])
