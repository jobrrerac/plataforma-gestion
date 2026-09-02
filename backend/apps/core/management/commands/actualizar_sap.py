"""Rellena el N° de persona SAP de los recursos desde una lista de RRHH.

Existe porque `cargar_recursos` no sirve para esto. Ese es un upsert completo:
además del SAP reescribe banda, grupos, `is_staff`, skills, clusters y tarifas,
y crea usuarios con contraseña si no existen. Pasarle un archivo de tres
columnas para tocar un solo campo dejaría el resto en manos de los valores por
defecto — es decir, arrasaría con lo que no viene en el archivo. Este comando
solo escribe `nro_persona_sap` y nada más.

DÓNDE VA EL ARCHIVO: en `backend/datos/`, que dentro del contenedor se ve como
`datos/`. Ver `backend/datos/README.md`.

Formato de entrada (TSV; la cabecera es opcional y se detecta sola):

    nombre <TAB> correo <TAB> nro_persona_sap

El correo es la clave: es único en `Recurso` y no depende de cómo se escriba el
nombre. El nombre viene igualmente y **se comprueba**: si no coincide con el
del recurso que sale por ese correo, el comando se detiene. Un archivo con las
columnas descuadradas por un copiar y pegar le pondría a cada persona el número
de otra, y eso no se nota mirando la aplicación.

Un número que ya está puesto y coincide se omite. Uno que ya está puesto y es
DISTINTO se reporta como conflicto y detiene la carga: puede ser una corrección
legítima o el archivo equivocado, y no es el comando quien tiene que decidirlo.
Con `--sobrescribir` se aplican esos cambios, siempre listándolos antes.

Uso:
    python manage.py actualizar_sap datos/personas_sap.tsv --simular
    python manage.py actualizar_sap datos/personas_sap.tsv --confirmar
"""

import csv
import sys
import unicodedata

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Recurso


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con los separadores colapsados.

    Compara "Peña-Ayala Juan-Camilo" con "Pena Ayala Juan Camilo" sin dar por
    distinto lo que solo cambia de tilde o de guion.
    """
    sin_tildes = unicodedata.normalize("NFKD", texto or "")
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    limpio = "".join(c if c.isalnum() else " " for c in sin_tildes.lower())
    return " ".join(limpio.split())


class Command(BaseCommand):
    help = (
        "Rellena el N° de persona SAP de los recursos desde un TSV "
        "(nombre, correo, nro_persona_sap). El archivo va en backend/datos/, "
        "que dentro del contenedor se ve como datos/. Solo toca ese campo."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "archivo",
            help="Ruta del TSV, relativa a /app (ej: datos/personas_sap.tsv). '-' lee de stdin.",
        )
        parser.add_argument("--simular", action="store_true", help="Muestra qué cambiaría, sin tocar nada.")
        parser.add_argument("--confirmar", action="store_true", help="Obligatorio para escribir de verdad.")
        parser.add_argument(
            "--sobrescribir", action="store_true",
            help="Aplica también los que ya tienen un número distinto (se listan igual).",
        )

    def handle(self, *args, **opciones):
        simular = opciones["simular"]
        if not simular and not opciones["confirmar"]:
            raise CommandError(
                "Esto modifica recursos. Usa --simular para ver los cambios, "
                "o --confirmar para aplicarlos."
            )

        filas = self._leer(opciones["archivo"])
        plan = [self._preparar(fila) for fila in filas]
        self._comprobar_numeros_repetidos(plan)

        conflictos = [p for p in plan if p["estado"] == "CONFLICTO"]
        a_escribir = [
            p for p in plan
            if p["estado"] == "PONE" or (p["estado"] == "CONFLICTO" and opciones["sobrescribir"])
        ]

        self._mostrar(plan)

        if conflictos and not opciones["sobrescribir"]:
            raise CommandError(
                f"{len(conflictos)} recurso(s) ya tienen un número SAP distinto del que trae el "
                "archivo. No se escribió nada. Si el archivo es el bueno, repite con "
                "--sobrescribir; si no, corregí el archivo."
            )

        if simular:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"Simulacro: se escribirían {len(a_escribir)} números. No se tocó nada."
            ))
            return

        with transaction.atomic():
            for p in a_escribir:
                recurso = p["recurso"]
                recurso.nro_persona_sap = p["sap"]
                # `update_fields` a propósito: que un error de este comando no
                # pueda arrastrar ningún otro campo del recurso.
                recurso.save(update_fields=["nro_persona_sap", "updated_at"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Actualizados {len(a_escribir)} recursos. No se tocó ningún otro campo."
        ))

    # ── lectura ─────────────────────────────────────────────────────────────

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
            if len(campos) < 3:
                raise CommandError(
                    f"La línea {numero} tiene {len(campos)} columnas; hacen falta 3 "
                    "(nombre, correo, nro_persona_sap) separadas por tabulador."
                )
            nombre, correo, sap = (c.strip() for c in campos[:3])
            # La cabecera se reconoce porque la 3ª columna no es un número.
            if numero == 1 and not sap.isdigit():
                continue
            if "@" not in correo:
                raise CommandError(f"Línea {numero}: '{correo}' no parece un correo.")
            if not sap.isdigit():
                raise CommandError(
                    f"Línea {numero}: el N° de persona SAP '{sap}' no es un número."
                )
            filas.append({"linea": numero, "nombre": nombre, "correo": correo, "sap": sap})
        if not filas:
            raise CommandError("El archivo no tiene ninguna fila utilizable.")
        return filas

    # ── comprobaciones por fila ─────────────────────────────────────────────

    def _preparar(self, fila):
        recurso = Recurso.all_objects.filter(email__iexact=fila["correo"]).first()
        if recurso is None:
            raise CommandError(
                f"Línea {fila['linea']}: no hay ningún recurso con el correo "
                f"'{fila['correo']}'."
            )
        if normalizar(recurso.nombre) != normalizar(fila["nombre"]):
            raise CommandError(
                f"Línea {fila['linea']}: el correo '{fila['correo']}' es de "
                f"'{recurso.nombre}', pero el archivo dice '{fila['nombre']}'. "
                "Si las columnas se descuadraron, cada persona acabaría con el "
                "número de otra. Corrige el archivo."
            )

        actual = (recurso.nro_persona_sap or "").strip()
        if not actual:
            estado = "PONE"
        elif actual == fila["sap"]:
            estado = "IGUAL"
        else:
            estado = "CONFLICTO"

        return {
            "linea": fila["linea"],
            "recurso": recurso,
            "sap": fila["sap"],
            "actual": actual,
            "estado": estado,
        }

    def _comprobar_numeros_repetidos(self, plan):
        """`nro_persona_sap` es único: dos personas con el mismo número reventarían
        a media escritura, y el mensaje de la base no diría de quién se trata."""
        por_numero = {}
        for p in plan:
            por_numero.setdefault(p["sap"], []).append(p["recurso"].nombre)
        repetidos = {sap: quienes for sap, quienes in por_numero.items() if len(quienes) > 1}
        if repetidos:
            detalle = "; ".join(f"{sap}: {', '.join(q)}" for sap, q in repetidos.items())
            raise CommandError(f"El archivo repite números SAP entre personas distintas — {detalle}")

        ajenos = []
        for p in plan:
            if p["estado"] == "IGUAL":
                continue
            otro = Recurso.all_objects.filter(nro_persona_sap=p["sap"]).exclude(pk=p["recurso"].pk).first()
            if otro is not None:
                ajenos.append(f"{p['sap']} ya es de {otro.nombre}, y el archivo se lo da a {p['recurso'].nombre}")
        if ajenos:
            raise CommandError("Números SAP que ya pertenecen a otro recurso — " + "; ".join(ajenos))

    # ── salida ──────────────────────────────────────────────────────────────

    def _mostrar(self, plan):
        marcas = {"PONE": "pone ", "IGUAL": "igual", "CONFLICTO": "OJO  "}
        for p in plan:
            linea = f"  {marcas[p['estado']]} {p['recurso'].nombre[:36]:36} {p['sap']}"
            if p["estado"] == "CONFLICTO":
                self.stdout.write(self.style.WARNING(
                    f"{linea}   <- ya tenia {p['actual']}"
                ))
            elif p["estado"] == "IGUAL":
                self.stdout.write(f"{linea}   (ya estaba)")
            else:
                self.stdout.write(linea)

        resumen = {estado: sum(1 for p in plan if p["estado"] == estado) for estado in marcas}
        self.stdout.write("")
        self.stdout.write(
            f"{len(plan)} filas: {resumen['PONE']} por poner, "
            f"{resumen['IGUAL']} ya estaban, {resumen['CONFLICTO']} en conflicto."
        )
