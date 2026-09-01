"""Carga solicitudes de recurso en bloque desde un plan de trabajo.

Hace lo mismo que la pantalla "Solicitar recurso", una fila a la vez, para no
tener que crear a mano un cronograma entero. Las asignaciones nacen SOLICITADAS
y pasan por la aprobación normal: este comando no aprueba nada ni se salta la
validación de capacidad.

Formato de entrada (TSV, una fila por tarea; es tal cual se copia de un Excel):

    recurso <TAB> actividad <TAB> fecha_inicio <TAB> fecha_fin <TAB> horas

  - recurso: el correo, o un nombre parcial que identifique a UNA sola persona.
    "Daniel Guzman" encuentra a "Guzman-Mejia Daniel-Fernando" porque contiene
    ambas palabras. Si el nombre encaja con dos personas el comando se detiene:
    adivinar a quién se le asigna trabajo no es una opción.
  - fechas: dd/mm/aaaa.
  - horas: TOTAL de la tarea en el rango, no horas por día. Coma o punto
    decimal, ambos valen.

La intensidad diaria sale de repartir esas horas entre los días hábiles del
rango (descontando fines de semana, feriados e indisponibilidades de esa
persona). Como `intensidad_diaria` guarda un solo decimal, el reparto se
redondea y el total efectivo puede desviarse unas décimas del pedido; cuando
pasa, se reporta fila por fila en vez de callarlo.

Uso:
    python manage.py cargar_asignaciones plan.tsv --proyecto V-25188808/Q \\
        --solicitante inetum_admin --simular
    python manage.py cargar_asignaciones plan.tsv --proyecto V-25188808/Q \\
        --solicitante inetum_admin --confirmar

Con "-" como archivo lee de la entrada estándar.
"""

import csv
import sys
import unicodedata
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import ceil

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.assignments.models import Asignacion, LogAuditoria
from apps.assignments.services import capacidad_maxima_dia, mapa_carga
from apps.calendar_engine.services import CalendarioRango, contar_dias_habiles
from apps.core.models import Proyecto, Recurso

# Estados en los que una asignación sigue "viva" y por tanto cuenta como que la
# persona ya está asignada a ese proyecto en ese rango. Una rechazada o revocada
# no bloquea: si se vuelve a cargar el plan, se crea otra vez a propósito.
ESTADOS_VIGENTES = ("SOLICITADA", "APROBADA")


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar nombres escritos de cualquier forma."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "")
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return sin_tildes.lower()


def partir_en_palabras(texto: str) -> list:
    """Separa un nombre en palabras, tratando guiones y puntos como espacios."""
    limpio = "".join(c if c.isalnum() else " " for c in normalizar(texto))
    return [p for p in limpio.split() if p]


def buscar_recurso(referencia: str):
    """Resuelve la referencia a un Recurso único, o explica por qué no puede.

    Por correo es exacto. Por nombre exige que TODAS las palabras aparezcan en
    el nombre del recurso y que el resultado sea único: con estos apellidos
    compuestos, "Santiago Martinez" encaja con dos personas distintas y un
    acierto por orden alfabético sería una asignación a la persona equivocada.
    """
    referencia = (referencia or "").strip()
    if not referencia:
        raise CommandError("Hay una fila sin recurso.")

    if "@" in referencia:
        recurso = Recurso.objects.filter(email__iexact=referencia).first()
        if recurso is None:
            raise CommandError(f"No hay ningún recurso con el correo '{referencia}'.")
        return recurso

    palabras = partir_en_palabras(referencia)
    candidatos = [
        r for r in Recurso.objects.all()
        if all(p in partir_en_palabras(r.nombre) for p in palabras)
    ]
    if not candidatos:
        raise CommandError(f"No hay ningún recurso que coincida con '{referencia}'.")
    if len(candidatos) > 1:
        nombres = ", ".join(f"{r.nombre} <{r.email}>" for r in candidatos)
        raise CommandError(
            f"'{referencia}' coincide con {len(candidatos)} recursos: {nombres}. "
            "Usá el correo en esa fila para que no haya duda."
        )
    return candidatos[0]


def leer_horas(valor: str) -> float:
    """Acepta '8,5' y '8.5'. Son horas totales de la tarea, no por día."""
    texto = (valor or "").strip().replace(",", ".")
    try:
        horas = float(texto)
    except ValueError:
        raise CommandError(f"'{valor}' no son unas horas válidas.")
    if horas <= 0:
        raise CommandError(f"Las horas deben ser mayores que 0 (llegó '{valor}').")
    return horas


def leer_fecha(valor: str):
    texto = (valor or "").strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise CommandError(f"'{valor}' no es una fecha válida (se espera dd/mm/aaaa).")


class Command(BaseCommand):
    help = "Crea solicitudes de recurso en bloque desde un plan de trabajo en TSV."

    def add_arguments(self, parser):
        parser.add_argument("archivo", help="TSV con el plan; '-' para leer de stdin.")
        parser.add_argument("--proyecto", required=True, help="Código del proyecto (ej: V-25188808/Q).")
        parser.add_argument(
            "--solicitante", required=True,
            help="Usuario que queda como quien solicita (username o correo).",
        )
        parser.add_argument("--simular", action="store_true", help="Muestra qué se crearía, sin tocar nada.")
        parser.add_argument("--confirmar", action="store_true", help="Obligatorio para crear de verdad.")

    def handle(self, *args, **opciones):
        simular = opciones["simular"]
        if not simular and not opciones["confirmar"]:
            raise CommandError(
                "Esto crea asignaciones. Usá --simular para ver el plan, "
                "o --confirmar para crearlas."
            )

        proyecto = self._buscar_proyecto(opciones["proyecto"])
        solicitante = self._buscar_solicitante(opciones["solicitante"])
        filas = self._leer_plan(opciones["archivo"])

        self.stdout.write("")
        self.stdout.write(f"Proyecto     {proyecto.codigo} — {proyecto.nombre}")
        self.stdout.write(f"Solicitante  {solicitante.username}")
        self.stdout.write(f"Filas        {len(filas)}")
        self.stdout.write("")

        plan = [self._preparar(fila, proyecto) for fila in filas]
        self._marcar_repetidas_del_propio_plan(plan)
        self._mostrar(plan)
        self._avisar_sobrecarga(plan, proyecto)

        a_crear = [p for p in plan if p["motivo_omision"] is None]
        if simular:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"Simulacro: se crearían {len(a_crear)} asignaciones. No se tocó nada."
            ))
            return

        with transaction.atomic():
            for p in a_crear:
                p["asignacion"] = self._crear(p, proyecto, solicitante)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Creadas {len(a_crear)} asignaciones SOLICITADAS en {proyecto.codigo}. "
            "Faltan aprobarlas."
        ))

    # ── resolución de referencias ───────────────────────────────────────────

    def _buscar_proyecto(self, codigo):
        proyecto = Proyecto.objects.filter(codigo__iexact=codigo.strip()).first()
        if proyecto is None:
            raise CommandError(f"No existe el proyecto '{codigo}'.")
        if proyecto.estado != "ACTIVO":
            raise CommandError(f"El proyecto '{codigo}' está {proyecto.estado}, no ACTIVO.")
        return proyecto

    def _buscar_solicitante(self, referencia):
        referencia = referencia.strip()
        usuario = (
            User.objects.filter(username__iexact=referencia).first()
            or User.objects.filter(email__iexact=referencia).first()
        )
        if usuario is None:
            raise CommandError(f"No existe el usuario '{referencia}'.")
        return usuario

    def _leer_plan(self, ruta):
        if ruta == "-":
            texto = sys.stdin.read()
        else:
            with open(ruta, encoding="utf-8-sig") as f:
                texto = f.read()

        filas = []
        for numero, campos in enumerate(csv.reader(texto.splitlines(), delimiter="\t"), start=1):
            if not any(c.strip() for c in campos):
                continue
            if len(campos) < 5:
                raise CommandError(
                    f"La línea {numero} tiene {len(campos)} columnas; hacen falta 5 "
                    "(recurso, actividad, fecha inicio, fecha fin, horas) separadas por tabulador."
                )
            # Una cabecera se reconoce porque la 3ª columna no es una fecha.
            if numero == 1:
                try:
                    leer_fecha(campos[2])
                except CommandError:
                    continue
            filas.append({
                "linea": numero,
                "recurso": campos[0].strip(),
                "actividad": campos[1].strip(),
                "fecha_inicio": campos[2].strip(),
                "fecha_fin": campos[3].strip(),
                "horas": campos[4].strip(),
            })
        if not filas:
            raise CommandError("El plan no tiene ninguna fila utilizable.")
        return filas

    # ── cálculo por fila ────────────────────────────────────────────────────

    def _preparar(self, fila, proyecto):
        recurso = buscar_recurso(fila["recurso"])
        fecha_inicio = leer_fecha(fila["fecha_inicio"])
        fecha_fin = leer_fecha(fila["fecha_fin"])
        horas_pedidas = leer_horas(fila["horas"])

        if fecha_fin < fecha_inicio:
            raise CommandError(
                f"Línea {fila['linea']}: la fecha fin ({fecha_fin}) es anterior "
                f"al inicio ({fecha_inicio})."
            )

        dias = contar_dias_habiles(fecha_inicio, fecha_fin, recurso)
        if dias == 0:
            raise CommandError(
                f"Línea {fila['linea']}: {recurso.nombre} no tiene ningún día hábil "
                f"entre {fecha_inicio} y {fecha_fin} (fin de semana, feriado o novedad)."
            )

        # Un solo decimal es lo que admite el campo; se redondea aquí para que
        # lo que se reporta sea lo que de verdad va a quedar guardado.
        exacta = Decimal(str(horas_pedidas)) / Decimal(dias)
        intensidad = exacta.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if intensidad <= 0:
            intensidad = Decimal("0.1")
        horas_efectivas = intensidad * Decimal(dias)

        existente = Asignacion.objects.filter(
            recurso=recurso, proyecto=proyecto,
            fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
            estado__in=ESTADOS_VIGENTES,
        ).first()

        return {
            "linea": fila["linea"],
            "recurso": recurso,
            "actividad": fila["actividad"],
            "fecha_inicio": fecha_inicio,
            "fecha_fin": fecha_fin,
            "dias": dias,
            "horas_pedidas": horas_pedidas,
            "intensidad": intensidad,
            "horas_efectivas": horas_efectivas,
            "desvio": horas_efectivas - Decimal(str(horas_pedidas)),
            "motivo_omision": (
                f"ya existe ({existente.estado}, #{existente.pk})" if existente else None
            ),
        }

    def _marcar_repetidas_del_propio_plan(self, plan):
        """Dos filas con la misma persona, proyecto y rango son la misma asignación.

        Sin esto, la comprobación contra la base de datos no las vería —dentro
        de la misma transacción aún no existe la primera— y quedarían duplicadas.
        """
        vistas = {}
        for p in plan:
            if p["motivo_omision"] is not None:
                continue
            clave = (p["recurso"].pk, p["fecha_inicio"], p["fecha_fin"])
            if clave in vistas:
                p["motivo_omision"] = f"repetida en el plan (línea {vistas[clave]})"
            else:
                vistas[clave] = p["linea"]

    # ── salida ──────────────────────────────────────────────────────────────

    def _mostrar(self, plan):
        for p in plan:
            marca = "OMITE" if p["motivo_omision"] else "crea "
            linea = (
                f"  {marca} {p['recurso'].nombre[:32]:32} "
                f"{p['fecha_inicio']:%d/%m} a {p['fecha_fin']:%d/%m}  "
                f"{p['dias']:2}d  {float(p['intensidad']):4.1f} h/d  "
                f"= {float(p['horas_efectivas']):5.1f} h   {p['actividad'][:44]}"
            )
            if p["motivo_omision"]:
                self.stdout.write(self.style.WARNING(f"{linea}  [{p['motivo_omision']}]"))
                continue
            self.stdout.write(linea)
            if p["desvio"]:
                self.stdout.write(self.style.WARNING(
                    f"        ajuste de redondeo: se pidieron {p['horas_pedidas']:g} h "
                    f"y quedan {float(p['horas_efectivas']):g} h "
                    f"({float(p['desvio']):+g} h)"
                ))

    def _avisar_sobrecarga(self, plan, proyecto):
        """Avisa de los días en que la persona pasaría de su jornada al aprobar.

        No bloquea: crear la solicitud está permitido igual que desde la
        pantalla, y es la aprobación la que rechaza la sobreasignación. Pero
        verlo ahora evita descubrirlo de a una cuando ya hay 20 creadas.
        """
        a_crear = [p for p in plan if p["motivo_omision"] is None]
        if not a_crear:
            return

        inicio = min(p["fecha_inicio"] for p in a_crear)
        fin = max(p["fecha_fin"] for p in a_crear)
        recursos = {p["recurso"].pk: p["recurso"] for p in a_crear}

        # Carga ya aprobada en la base + la que sumaría este plan.
        carga = mapa_carga(list(recursos), inicio, fin)
        cal = CalendarioRango(inicio, fin, list(recursos.values()))
        for p in a_crear:
            fecha = p["fecha_inicio"]
            while fecha <= p["fecha_fin"]:
                if cal.es_habil(fecha, p["recurso"]):
                    por_dia = carga.setdefault(p["recurso"].pk, {})
                    por_dia[fecha] = por_dia.get(fecha, 0.0) + float(p["intensidad"])
                fecha += timedelta(days=1)

        avisos = []
        for rid, por_dia in carga.items():
            for fecha, horas in sorted(por_dia.items()):
                tope = capacidad_maxima_dia(fecha)
                if horas > tope:
                    avisos.append(
                        f"  {recursos[rid].nombre[:32]:32} {fecha:%d/%m/%Y}  "
                        f"{horas:.1f} h sobre un tope de {tope:g} h"
                    )
        if avisos:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Días que superarían la jornada al aprobar (la solicitud se crea igual):"
            ))
            for aviso in avisos:
                self.stdout.write(self.style.WARNING(aviso))

    # ── escritura ───────────────────────────────────────────────────────────

    def _crear(self, p, proyecto, solicitante):
        asignacion = Asignacion.objects.create(
            recurso=p["recurso"],
            proyecto=proyecto,
            modo_asignacion="RANGO",
            fecha_inicio=p["fecha_inicio"],
            fecha_fin=p["fecha_fin"],
            dias_habiles=p["dias"],
            horas_totales=ceil(float(p["horas_efectivas"])),
            intensidad_diaria=p["intensidad"],
            jornada_completa=False,
            estado="SOLICITADA",
            solicitada_por=solicitante,
        )
        # La actividad no es un campo de Asignacion: el modelo asigna personas a
        # proyectos, no a tareas. Va al log —que es append-only— para no perder
        # de qué tarea del cronograma salió cada asignación.
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="CREAR", actor=solicitante,
            detalle={
                "modo": "RANGO",
                "origen": "carga_masiva",
                "actividad": p["actividad"],
                "dias_habiles": p["dias"],
                "horas_totales": asignacion.horas_totales,
                "horas_pedidas": p["horas_pedidas"],
                "intensidad_diaria": float(p["intensidad"]),
            },
        )
        return asignacion
