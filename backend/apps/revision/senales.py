"""Reglas deterministas que ordenan la cola de aprobación de horas.

Cien renglones al día no es un problema de lectura, es de triaje. Estas reglas
no aprueban ni rechazan nada: reparten la cola en tres carriles para que quien
firma empiece por lo que merece su atención.

Todo lo de aquí es aritmética sobre datos que ya existen. Ninguna llamada
externa, ningún modelo de lenguaje, nada que pueda estar caído. Si mañana se
añade una capa que use un LLM, será encima de esto, y esto seguirá funcionando
sin ella.

**Las bandas ordenan, no deciden.** La pantalla enseña los mismos botones en los
tres carriles: nada queda oculto ni preseleccionado, y el motivo va siempre
escrito para que se pueda contradecir.

Dos reglas del diseño no llegaron aquí, y conviene decir por qué:

- *«el día no cuadra con la jornada»*: `registrar_dia()` ya lo impide, así que
  un día REGISTRADO siempre cuadra. Sería código muerto.
- *«racha de devoluciones»*: se calcula, pero **no cambia la banda**. Marcar
  todos los renglones de alguien porque el mes pasado le devolvieron dos es
  ruidoso y se lee como un reproche. Va como contexto del día, que es donde
  ayuda sin acusar.
"""

import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta

RUTINA = "RUTINA"
REVISAR = "REVISAR"
ATENCION = "ATENCION"

# De menos a más urgente. La banda de un día es la peor de sus renglones.
ORDEN = {RUTINA: 0, REVISAR: 1, ATENCION: 2}

ETIQUETAS = {
    RUTINA: "Rutina",
    REVISAR: "Revisar",
    ATENCION: "Atención",
}

# Un detalle por debajo de esto no permite legalizar nada. «muchas tareas» son
# 13 caracteres y dos palabras; «Notebooks de precarga y ajustes de pipeline»,
# 43 y siete. El corte separa los dos casos con holgura.
MINIMO_CARACTERES = 25
MINIMO_PALABRAS = 3

# Por debajo de esta fracción de la jornada, unas horas no facturables son
# normales; a partir de ahí se mira. El renglón de estudio de 7,5 h sobre 8,5 y
# el de «Actividades Departamentales» de 8,5 sobre 8,5 caen los dos aquí.
FRACCION_NO_FACTURABLE = 0.5

# Ventana para considerar que un detalle se copió de otro día.
DIAS_REPETICION = 14

# Contexto de devoluciones: cuántas y en cuánto tiempo.
DIAS_DEVOLUCIONES = 30
MINIMO_DEVOLUCIONES = 2

# Margen antes de decir que alguien se pasó del plan. Media hora es la unidad
# mínima de registro, así que por debajo no hay nada que discutir.
MARGEN_HORAS = 0.5


@dataclass
class Senal:
    """Un motivo concreto, con su banda y su explicación en una frase.

    **`informativa` es una señal que se enseña pero no pide nada.** No sube la
    banda del día, no pinta ámbar y aprobar el renglón no cuenta como forzar
    nada. Existe porque una regla puede acertar en qué mirar y equivocarse en
    cuánto insistir, y son dos cosas distintas que antes iban juntas.
    """
    codigo: str
    banda: str
    texto: str
    informativa: bool = False


@dataclass
class Evaluacion:
    senales: list = field(default_factory=list)

    @property
    def accionables(self):
        """Las que piden una decisión. Las informativas solo acompañan."""
        return [s for s in self.senales if not s.informativa]

    @property
    def banda(self):
        # Sale de las accionables: una señal informativa no puede sacar un
        # renglón de Rutina, o volveríamos a tener el carril de atención lleno
        # de días normales.
        if not self.accionables:
            return RUTINA
        return max((s.banda for s in self.accionables), key=lambda b: ORDEN[b])

    @property
    def etiqueta(self):
        return ETIQUETAS[self.banda]


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con los espacios colapsados."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "")
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return " ".join(sin_tildes.lower().split())


# ── Las reglas ──────────────────────────────────────────────────────────────
#
# Cada una recibe el renglón, su día y el contexto precargado, y devuelve una
# Senal o None. Se mantienen separadas para poder probarlas de una en una y
# para que añadir la siguiente no obligue a tocar las anteriores.


def sin_plan(registro, dia, ctx):
    """Horas a un proyecto de cliente al que esa persona no estaba asignada.

    La pantalla de registro solo ofrece proyectos facturables con asignación
    aprobada que cubra el día, así que esto no debería poder pasar. Que pase
    significa que la asignación se revocó después, o que el renglón entró por
    otra vía. En ambos casos hay que mirarlo.

    Los internos quedan fuera a propósito: nadie recibe una asignación a
    «Departamentales» y aun así todo el mundo puede imputarles horas.
    """
    if not registro.proyecto_id or not registro.proyecto.facturable:
        return None
    if ctx.horas_planificadas(dia.recurso_id, registro.proyecto_id, dia.fecha) is not None:
        return None
    return Senal(
        "SIN_PLAN", ATENCION,
        f"No tenía asignación aprobada a {registro.proyecto.codigo} el "
        f"{dia.fecha:%d/%m/%Y}.",
    )


def sobre_plan(registro, dia, ctx):
    """Declaró bastante más de lo que el plan preveía para ese día."""
    if not registro.proyecto_id or not registro.proyecto.facturable:
        return None
    planificadas = ctx.horas_planificadas(dia.recurso_id, registro.proyecto_id, dia.fecha)
    if planificadas is None:
        return None
    declaradas = float(registro.horas)
    if declaradas <= planificadas + MARGEN_HORAS:
        return None
    return Senal(
        "SOBRE_PLAN", REVISAR,
        f"Declaró {declaradas:g} h y el plan preveía {planificadas:g} h/día "
        f"en este proyecto.",
    )


def no_facturable_media_jornada(registro, dia, ctx):
    """Media jornada o más en algo que no se cobra a nadie. **Informativa.**

    Es la regla que caza los dos casos que motivaron el módulo: 7,5 h para
    escoger una certificación, y una jornada entera en «Actividades
    Departamentales» descrita como «muchas tareas».

    **Por qué dejó de pedir una decisión, con los números de la primera semana
    en producción:**

        regla                          salta   devoluciones que atrapó
        NO_FACTURABLE_MEDIA_JORNADA       47        3 de 3   (100%)
        DETALLE_REPETIDO                  29        1 de 3
        DETALLE_POBRE                     20        0 de 3

    Es la única que no se perdió nada de lo que un humano rechazó de verdad —
    pero para conseguirlo grita 44 veces de más. La mitad de todas las
    aprobaciones forzadas eran solo esto, y una regla que se anula el 94% de
    las veces enseña a ignorar también las que aciertan.

    Se pensó en quitarla solo para proyectos internos, que era de donde venía
    la mayor parte del ruido. Los datos lo desaconsejaron: una de las tres
    devoluciones fue precisamente 5 h en INT-DEPART descritas como «modulo de
    aprobación de recursos», devueltas por «no cumple con la especificidad de
    la tarea». Esa exención habría cegado justo ese caso.

    Así que se queda, se enseña, y no pide nada. Lo que sí conserva es que un
    día con esto **no se puede firmar de un clic** (ver `bloque_del_dia`): ahí
    hay algo que mirar, aunque casi siempre esté bien.

    Lo que estos tres rechazos piden de verdad no es una regla de duración. Los
    tres motivos dicen lo mismo —«no dijiste qué»— y uno de ellos tenía 227
    caracteres y 31 palabras, así que no hay umbral de longitud que lo separe.
    Eso es la fase 4.
    """
    if registro.facturable:
        return None
    jornada = float(dia.jornada_esperada or 0)
    if jornada <= 0:
        return None
    fraccion = float(registro.horas) / jornada
    if fraccion < FRACCION_NO_FACTURABLE:
        return None
    destino = registro.proyecto.codigo if registro.proyecto_id else registro.tipo_actividad.nombre
    return Senal(
        "NO_FACTURABLE_MEDIA_JORNADA", REVISAR,
        f"{float(registro.horas):g} h de {jornada:g} ({fraccion:.0%} del día) "
        f"en {destino}, que no es facturable.",
        informativa=True,
    )


def no_facturable_con_plan_lleno(registro, dia, ctx):
    """Horas no facturables cuando el día ya estaba planificado al completo.

    Si el plan decía jornada entera en proyectos y aun así aparecen horas de
    formación o internas, o el plan se corrió o desplazaron trabajo de cliente.
    Cualquiera de las dos merece una pregunta antes de firmar.
    """
    if registro.facturable:
        return None
    jornada = float(dia.jornada_esperada or 0)
    plan_total = ctx.plan_del_dia(dia.recurso_id, dia.fecha)
    if jornada <= 0 or plan_total < jornada:
        return None
    return Senal(
        "NO_FACTURABLE_CON_PLAN_LLENO", ATENCION,
        f"El plan ya ocupaba la jornada completa ({plan_total:g} h en proyectos) "
        f"y estas horas no son facturables.",
    )


def detalle_pobre(registro, dia, ctx):
    """El texto no alcanza para legalizar las horas que respalda."""
    texto = (registro.detalle or "").strip()
    palabras = len(texto.split())
    if len(texto) >= MINIMO_CARACTERES and palabras >= MINIMO_PALABRAS:
        return None
    return Senal(
        "DETALLE_POBRE", REVISAR,
        f"«{texto}» no dice qué se hizo: {len(texto)} caracteres, "
        f"{palabras} palabra{'s' if palabras != 1 else ''}.",
    )


def detalle_repetido(registro, dia, ctx):
    """El mismo texto, palabra por palabra, en otro día reciente.

    No es necesariamente un problema —hay trabajo que se parece de un día a
    otro— pero copiar el renglón anterior es la forma más común de rellenar sin
    describir, y quien firma debería verlo.
    """
    texto = normalizar(registro.detalle)
    if not texto:
        return None
    otras = ctx.dias_con_ese_detalle(dia.recurso_id, texto, dia.fecha)
    if not otras:
        return None
    fechas = ", ".join(f"{f:%d/%m}" for f in otras[:3])
    return Senal(
        "DETALLE_REPETIDO", REVISAR,
        f"El mismo texto, palabra por palabra, en {fechas}.",
    )


REGLAS = [
    sin_plan,
    sobre_plan,
    no_facturable_con_plan_lleno,
    no_facturable_media_jornada,
    detalle_pobre,
    detalle_repetido,
]


def evaluar(registro, dia, ctx) -> Evaluacion:
    """Aplica todas las reglas a un renglón. Sin ninguna señal, es rutina."""
    senales = [s for s in (regla(registro, dia, ctx) for regla in REGLAS) if s]
    return Evaluacion(senales=senales)


def ventana_repeticion(fecha):
    return fecha - timedelta(days=DIAS_REPETICION), fecha + timedelta(days=DIAS_REPETICION)


def ventana_devoluciones(fecha):
    return fecha - timedelta(days=DIAS_DEVOLUCIONES)
