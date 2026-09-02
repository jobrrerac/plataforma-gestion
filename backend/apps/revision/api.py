"""Punto de entrada del triaje: clasifica una cola de aprobación ya cargada.

`legalizacion` llama aquí con un import diferido y tolerante: si esta app no
está instalada, la cola se pinta como siempre. Esa es la razón de que el módulo
viva aparte — no para desplegarlo por su cuenta, que sigue siendo el mismo
monolito, sino para poder apagarlo sin migración de vuelta.

Todo el contexto se precarga en tres consultas fijas, sean diez renglones o
doscientos. Una consulta por fila convertiría una pantalla en una espera, y es
justo la pantalla que ya sufre por tener cien filas.
"""

from collections import defaultdict

from apps.assignments.models import Asignacion
from apps.legalizacion.models import RegistroHoras

from . import precedentes as prec
from . import senales as sn


class Contexto:
    """Lo que las reglas necesitan saber, cargado de una vez para toda la cola."""

    def __init__(self, dias):
        self.dias = dias
        self._plan = defaultdict(float)          # (recurso, proyecto, fecha) -> h/día
        self._plan_dia = defaultdict(float)      # (recurso, fecha) -> h/día en proyectos
        self._detalles = defaultdict(set)        # (recurso, texto) -> {fechas}
        self._devoluciones = defaultdict(int)    # recurso -> nº devueltos recientes
        if dias:
            self._cargar()

    # ── carga ───────────────────────────────────────────────────────────────

    def _cargar(self):
        # Un conjunto, no una lista: la cola trae un día por persona, así que la
        # misma fecha aparece tantas veces como gente registró ese día. Con la
        # lista, el plan de cada uno se sumaba una vez por repetición y salían
        # cifras imposibles —25,5 h planificadas en una jornada de 8,5—, lo que
        # además apagaba `SOBRE_PLAN` justo cuando debía saltar.
        fechas = sorted({d.fecha for d in self.dias})
        recursos = {d.recurso_id for d in self.dias}
        desde, hasta = fechas[0], fechas[-1]

        # 1. El plan: asignaciones aprobadas que cubren alguna de esas fechas.
        #    Solo APROBADAS — una solicitud pendiente no autoriza a imputar.
        asignaciones = Asignacion.objects.filter(
            recurso_id__in=recursos, estado="APROBADA",
            fecha_inicio__lte=hasta, fecha_fin__gte=desde,
        ).values_list(
            "recurso_id", "proyecto_id", "fecha_inicio", "fecha_fin",
            "intensidad_diaria", "jornada_completa",
        )
        for recurso_id, proyecto_id, inicio, fin, intensidad, jornada_completa in asignaciones:
            for fecha in fechas:
                if not (inicio <= fecha <= fin):
                    continue
                # `jornada_completa` guarda un 8.0 de relleno en intensidad y la
                # carga real es el tope del día; se pide al motor para no
                # duplicar aquí la regla de lunes-jueves 8,5 y viernes 8.
                if jornada_completa:
                    from apps.assignments.services import capacidad_maxima_dia
                    horas = capacidad_maxima_dia(fecha)
                else:
                    horas = float(intensidad or 0)
                self._plan[(recurso_id, proyecto_id, fecha)] += horas
                self._plan_dia[(recurso_id, fecha)] += horas

        # 2. Detalles de otros días, para detectar el copiar y pegar.
        desde_rep, hasta_rep = sn.ventana_repeticion(desde)
        _, hasta_rep = sn.ventana_repeticion(hasta)
        otros = RegistroHoras.objects.filter(
            dia__recurso_id__in=recursos,
            dia__fecha__gte=desde_rep, dia__fecha__lte=hasta_rep,
        ).values_list("dia__recurso_id", "dia__fecha", "detalle")
        for recurso_id, fecha, detalle in otros:
            self._detalles[(recurso_id, sn.normalizar(detalle))].add(fecha)

        # 3. Devoluciones recientes: contexto del día, no señal del renglón.
        devueltos = RegistroHoras.objects.filter(
            dia__recurso_id__in=recursos,
            estado=RegistroHoras.DEVUELTO,
            dia__fecha__gte=sn.ventana_devoluciones(desde),
        ).values_list("dia__recurso_id", flat=True)
        for recurso_id in devueltos:
            self._devoluciones[recurso_id] += 1

    # ── lo que preguntan las reglas ─────────────────────────────────────────

    def horas_planificadas(self, recurso_id, proyecto_id, fecha):
        """Horas que el plan preveía ese día en ese proyecto, o None si no había plan."""
        return self._plan.get((recurso_id, proyecto_id, fecha))

    def plan_del_dia(self, recurso_id, fecha):
        """Total de horas planificadas en proyectos ese día."""
        return self._plan_dia.get((recurso_id, fecha), 0.0)

    def dias_con_ese_detalle(self, recurso_id, texto_normalizado, excepto):
        """Otras fechas en las que esa persona escribió exactamente lo mismo."""
        fechas = self._detalles.get((recurso_id, texto_normalizado), set())
        return sorted(f for f in fechas if f != excepto)

    def devoluciones_recientes(self, recurso_id):
        return self._devoluciones.get(recurso_id, 0)


def bloque_del_dia(dia, usuario, pendientes=None) -> str:
    """Qué firma en bloque admite este día: ninguna, limpia o forzada.

    Devuelve `""`, `"LIMPIO"` o `"FORZADO"`. Tres condiciones estructurales
    valen para las dos formas —Admin, todo interno, más de un renglón— y lo
    único que las separa es si queda algún aviso encima de la mesa.

    Que el forzado exista no vacía el triaje: solo se ofrece donde ya se
    ofrecía el limpio, sigue sin tocar horas de cliente, y pide un motivo que
    se guarda con los códigos anulados. La diferencia con marcar las casillas
    una a una es real: allí se mira cada renglón, aquí no.
    """
    from apps.accounts.roles import es_admin
    from apps.legalizacion.models import RegistroHoras

    if not es_admin(usuario):
        return ""

    # `pendientes` se recibe ya evaluado cuando quien llama tiene los objetos en
    # la mano. Releerlos de la base traeria instancias distintas, sin la
    # evaluacion puesta, y entonces esto diria que no siempre.
    if pendientes is None:
        pendientes = [r for r in dia.registros.all() if r.estado == RegistroHoras.PENDIENTE]
    if len(pendientes) < 2:
        return ""
    if any(r.facturable for r in pendientes):
        return ""

    evaluados = [getattr(r, "evaluacion", None) for r in pendientes]
    if any(e is None for e in evaluados):
        return ""
    return "LIMPIO" if all(e.banda == sn.RUTINA for e in evaluados) else "FORZADO"


def aprobable_en_bloque(dia, usuario, pendientes=None) -> bool:
    """Si este día se puede firmar de una vez, sin mirar renglón a renglón.

    Cuatro condiciones, y hacen falta las cuatro:

    1. **Solo Admin.** Un PM responde por su proyecto; firmar un día entero de
       otra persona no es lo mismo que firmar lo suyo.
    2. **Todo lo pendiente del día no es facturable.** Si queda un renglón de
       cliente sin firmar, esto no es «el día»: es una parte, y la otra la debe
       ver su PM.
    3. **Todos en Rutina.** Es la versión comprobable de «los comentarios son
       atómicos, se ajustan a la tarea y son descriptivos»: ningún detalle pobre,
       ningún texto copiado de otro día, ningún plan que ya ocupaba la jornada.
    4. **Más de un renglón.** Con uno solo, el botón de siempre hace lo mismo.

    Un día interno de un solo renglón gordo no califica —`NO_FACTURABLE_MEDIA_
    JORNADA` lo saca de Rutina— y así debe ser: ahí hay algo que mirar. Lo que
    califica es el día partido en tareas concretas y descritas, que es
    precisamente cuando revisarlo de a una no aporta nada.

    Esto decide si el botón se ofrece. Que se pueda pulsar no autoriza nada: el
    servicio vuelve a comprobarlo todo antes de escribir.
    """
    return bloque_del_dia(dia, usuario, pendientes) == "LIMPIO"


def clasificar(dias, usuario=None):
    """Anota cada renglón con su evaluación y cada día con su banda.

    Muta los objetos que recibe, igual que `dias_por_aprobar` ya hace con
    `pendientes_mios` y `detalle`. Devuelve el recuento por banda para la
    cabecera de la pantalla.
    """
    ctx = Contexto(dias)
    recuento = {sn.RUTINA: 0, sn.REVISAR: 0, sn.ATENCION: 0}

    for dia in dias:
        for registro in list(dia.pendientes_mios) + list(dia.otros):
            registro.evaluacion = sn.evaluar(registro, dia, ctx)

        # La banda del día es la peor de lo que esta persona puede firmar: no
        # tiene sentido subir un día al carril de atención por un renglón de
        # otro PM que este ni siquiera puede tocar.
        propios = [r.evaluacion.banda for r in dia.pendientes_mios]
        dia.banda = max(propios, key=lambda b: sn.ORDEN[b]) if propios else sn.RUTINA
        dia.banda_etiqueta = sn.ETIQUETAS[dia.banda]
        dia.devoluciones_recientes = (
            ctx.devoluciones_recientes(dia.recurso_id)
            if ctx.devoluciones_recientes(dia.recurso_id) >= sn.MINIMO_DEVOLUCIONES
            else 0
        )
        for registro in dia.pendientes_mios:
            recuento[registro.evaluacion.banda] += 1
        dia.n_avisos = sum(1 for r in dia.pendientes_mios if r.evaluacion.senales)
        # Se le pasan los renglones ya evaluados. Dejar que los relea de la base
        # traeria instancias sin `evaluacion` y el boton no se ofreceria nunca;
        # hoy funciona solo porque el prefetch devuelve estos mismos objetos, y
        # eso es una casualidad de la que no conviene depender.
        pendientes = [
            r for r in list(dia.pendientes_mios) + list(dia.otros)
            if r.estado == RegistroHoras.PENDIENTE
        ]
        dia.bloque = bloque_del_dia(dia, usuario, pendientes) if usuario is not None else ""
        dia.aprobable_en_bloque = dia.bloque == "LIMPIO"
        dia.forzable_en_bloque = dia.bloque == "FORZADO"

    # Lo que necesita mirarse primero, primero. Dentro de cada banda se conserva
    # el orden que traía (fecha y nombre), que es el que hace la lista legible.
    dias.sort(key=lambda d: -sn.ORDEN[d.banda])

    # Precedentes: qué declaró antes esta persona para algo parecido. Va después
    # de ordenar para que el tope de renglones se gaste en los que salen arriba.
    prec.adjuntar(dias)
    return recuento
