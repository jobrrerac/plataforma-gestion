"""Carga en la plataforma las novedades que estaban en un Excel.

Es una migración de una vez, no un flujo permanente: a partir de ahora las
novedades se piden desde `/novedades/` y las aprueba un Admin. Esto existe para
que lo que ya estaba aprobado en la hoja de cálculo deje de vivir solo ahí.

DÓNDE VA EL ARCHIVO: en `backend/datos/`, que dentro del contenedor se ve como
`datos/`. Ver `backend/datos/README.md`.

Formato (TSV; la cabecera es opcional):

    nombre <TAB> correo <TAB> fecha_inicio <TAB> fecha_fin <TAB> estado <TAB> notas

Tres decisiones que conviene tener a la vista:

**Solo lo aprobado y solo lo que aún aplica.** Se cargan las filas en estado
«Aprobado» cuya `fecha_fin` sea de hoy en adelante. Una ausencia que ya terminó
no cambia nada: no descuenta capacidad futura ni libera días que ya pasaron.
Unas vacaciones en curso sí, y por eso el corte mira la fecha de fin y no la de
inicio.

**Nacen APROBADAS.** Ya lo estaban en el Excel; volver a pedirlas y aprobarlas
una por una sería teatro, y mientras tanto la capacidad estaría mal. Se deja
`solicitada_por` vacío, que es justo lo que el modelo documenta para lo cargado
antes de existir el flujo.

**Los medios días no se cargan.** `Indisponibilidad` trabaja con días
completos. Registrar medio día como día entero haría desaparecer esa jornada de
`/horas/`, y esa persona no podría legalizar la mitad que sí trabajó — peor que
no tenerlo. Se listan aparte para que se resuelvan a mano.

Uso:
    python manage.py migrar_novedades datos/novedades_excel.tsv --simular
    python manage.py migrar_novedades datos/novedades_excel.tsv --confirmar
"""

import csv
import sys
import unicodedata
from datetime import datetime

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.calendar_engine.models import Indisponibilidad
from apps.core.models import Recurso

# Estados en los que una novedad ya ocupa esas fechas. Una rechazada no cuenta.
ESTADOS_VIGENTES = ("PENDIENTE", "APROBADA")


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto or "")
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return " ".join(sin_tildes.lower().split())


def leer_fecha(valor: str):
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime((valor or "").strip(), formato).date()
        except ValueError:
            continue
    raise CommandError(f"'{valor}' no es una fecha válida (se espera dd/mm/aaaa).")


def deducir_tipo(notas: str) -> str:
    """El Excel no trae tipo: se deduce de la nota.

    El modelo solo distingue vacaciones de permiso. Cumpleaños, Día de la
    Familia, revitalización o asuntos personales son todos permisos: lo que los
    diferencia es la política de RRHH, no lo que la plataforma necesita saber.
    """
    return "VACACION" if "vacacion" in normalizar(notas) else "PERMISO"


def es_medio_dia(notas: str) -> bool:
    return "medio dia" in normalizar(notas)


class Command(BaseCommand):
    help = (
        "Carga de una vez las novedades aprobadas que estaban en un Excel. "
        "Solo las que aún aplican (fecha fin de hoy en adelante). "
        "El archivo va en backend/datos/."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "archivo",
            help="Ruta del TSV, relativa a /app (ej: datos/novedades_excel.tsv). '-' lee de stdin.",
        )
        parser.add_argument(
            "--revisor", default=None,
            help="Usuario que queda como quien aprobó (username o correo). Por defecto, ninguno.",
        )
        parser.add_argument("--simular", action="store_true", help="Muestra qué se crearía, sin tocar nada.")
        parser.add_argument("--confirmar", action="store_true", help="Obligatorio para crear de verdad.")

    def handle(self, *args, **opciones):
        simular = opciones["simular"]
        if not simular and not opciones["confirmar"]:
            raise CommandError(
                "Esto crea novedades aprobadas, que descuentan capacidad. "
                "Usa --simular para ver el plan, o --confirmar para crearlas."
            )

        revisor = self._buscar_revisor(opciones["revisor"])
        hoy = timezone.localdate()
        filas = self._leer(opciones["archivo"])

        self.stdout.write("")
        self.stdout.write(f"Hoy      {hoy:%d/%m/%Y} — solo se cargan las que terminan hoy o después")
        self.stdout.write(f"Filas    {len(filas)}")
        self.stdout.write("")

        plan = [self._preparar(fila, hoy) for fila in filas]
        self._mostrar(plan)

        a_crear = [p for p in plan if p["accion"] == "CREA"]
        if simular:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"Simulacro: se crearían {len(a_crear)} novedades. No se tocó nada."
            ))
            return

        with transaction.atomic():
            for p in a_crear:
                Indisponibilidad.objects.create(
                    recurso=p["recurso"],
                    fecha_inicio=p["inicio"],
                    fecha_fin=p["fin"],
                    tipo=p["tipo"],
                    origen="MANUAL",
                    estado="APROBADA",
                    motivo=p["notas"][:200],
                    # Vacío a propósito: nadie la pidió por la aplicación. Es lo
                    # que el propio modelo documenta para lo cargado antes del flujo.
                    solicitada_por=None,
                    revisada_por=revisor,
                    revisada_en=timezone.now(),
                )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Creadas {len(a_crear)} novedades APROBADAS. Ya descuentan capacidad."
        ))

    # ── lectura ─────────────────────────────────────────────────────────────

    def _buscar_revisor(self, referencia):
        if not referencia:
            return None
        usuario = (
            User.objects.filter(username__iexact=referencia).first()
            or User.objects.filter(email__iexact=referencia).first()
        )
        if usuario is None:
            raise CommandError(f"No existe el usuario '{referencia}'.")
        return usuario

    def _leer(self, ruta):
        if ruta == "-":
            texto = sys.stdin.read()
        else:
            with open(ruta, encoding="utf-8-sig") as f:
                texto = f.read()

        filas = []
        for numero, campos in enumerate(csv.reader(texto.splitlines(), delimiter="\t"), start=1):
            if not any(c.strip() for c in campos):
                continue
            if len(campos) < 6:
                raise CommandError(
                    f"La línea {numero} tiene {len(campos)} columnas; hacen falta 6 "
                    "(nombre, correo, fecha inicio, fecha fin, estado, notas)."
                )
            nombre, correo, inicio, fin, estado, notas = (c.strip() for c in campos[:6])
            # La cabecera se reconoce porque la 3ª columna no es una fecha.
            if numero == 1:
                try:
                    leer_fecha(inicio)
                except CommandError:
                    continue
            filas.append({
                "linea": numero, "nombre": nombre, "correo": correo,
                "inicio": inicio, "fin": fin, "estado": estado, "notas": notas,
            })
        if not filas:
            raise CommandError("El archivo no tiene ninguna fila utilizable.")
        return filas

    # ── decisión por fila ───────────────────────────────────────────────────

    def _preparar(self, fila, hoy):
        base = {
            "linea": fila["linea"], "nombre": fila["nombre"], "notas": fila["notas"],
            "inicio": None, "fin": None, "recurso": None, "tipo": None,
        }

        if normalizar(fila["estado"]) != "aprobado":
            return {**base, "accion": "OMITE", "motivo": f"estado «{fila['estado']}», no aprobado"}

        inicio = leer_fecha(fila["inicio"])
        fin = leer_fecha(fila["fin"])
        if fin < inicio:
            raise CommandError(f"Línea {fila['linea']}: la fecha fin es anterior al inicio.")
        base.update(inicio=inicio, fin=fin)

        if fin < hoy:
            return {**base, "accion": "OMITE", "motivo": f"terminó el {fin:%d/%m/%Y}, ya no aplica"}

        if es_medio_dia(fila["notas"]):
            return {
                **base, "accion": "MANO",
                "motivo": "es medio día y el modelo solo maneja días completos",
            }

        recurso = Recurso.all_objects.filter(email__iexact=fila["correo"]).first()
        if recurso is None:
            raise CommandError(
                f"Línea {fila['linea']}: no hay ningún recurso con el correo '{fila['correo']}'."
            )
        if normalizar(recurso.nombre) != normalizar(fila["nombre"]):
            raise CommandError(
                f"Línea {fila['linea']}: el correo '{fila['correo']}' es de "
                f"'{recurso.nombre}', pero el archivo dice '{fila['nombre']}'."
            )
        base.update(recurso=recurso, tipo=deducir_tipo(fila["notas"]))

        # Solape, no igualdad: si alguien ya pidió esos días por la aplicación,
        # las fechas pueden no coincidir exactamente y aun así ser la misma
        # ausencia. Duplicarla descontaría capacidad dos veces.
        existente = Indisponibilidad.objects.filter(
            Q(recurso=recurso, estado__in=ESTADOS_VIGENTES)
            & Q(fecha_inicio__lte=fin, fecha_fin__gte=inicio)
        ).first()
        if existente:
            return {
                **base, "accion": "OMITE",
                "motivo": (
                    f"ya existe ({existente.estado} #{existente.pk}, "
                    f"{existente.fecha_inicio:%d/%m}–{existente.fecha_fin:%d/%m})"
                ),
            }

        return {**base, "accion": "CREA", "motivo": ""}

    # ── salida ──────────────────────────────────────────────────────────────

    def _mostrar(self, plan):
        etiquetas = {"VACACION": "Vacaciones", "PERMISO": "Permiso"}
        marcas = {"CREA": "crea ", "OMITE": "omite", "MANO": "MANO "}
        estilos = {
            "CREA": lambda t: t,
            "OMITE": self.style.WARNING,
            "MANO": self.style.ERROR,
        }
        for p in plan:
            rango = (
                f"{p['inicio']:%d/%m/%Y} a {p['fin']:%d/%m/%Y}" if p["inicio"] else " " * 22
            )
            linea = (
                f"  {marcas[p['accion']]} {p['nombre'][:33]:33} {rango}  "
                f"{etiquetas.get(p['tipo'], ''):10} {p['notas'][:34]}"
            )
            if p["motivo"]:
                linea += f"   <- {p['motivo']}"
            self.stdout.write(estilos[p["accion"]](linea))

        a_mano = [p for p in plan if p["accion"] == "MANO"]
        if a_mano:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR(
                "Estas hay que resolverlas a mano: el modelo no tiene medio día, y "
                "cargarlas como día completo dejaría a esa persona sin poder "
                "legalizar la mitad que sí trabajó."
            ))

        resumen = {a: sum(1 for p in plan if p["accion"] == a) for a in ("CREA", "OMITE", "MANO")}
        self.stdout.write("")
        self.stdout.write(
            f"{len(plan)} filas: {resumen['CREA']} por crear, "
            f"{resumen['OMITE']} omitidas, {resumen['MANO']} a mano."
        )
