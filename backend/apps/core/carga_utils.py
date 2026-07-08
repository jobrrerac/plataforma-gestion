"""Utilidades compartidas por los comandos de carga masiva (recursos y proyectos)."""
import csv
import secrets
import string
from datetime import datetime

_VERDADEROS = {"si", "sí", "s", "true", "1", "x", "yes", "y", "verdadero"}
_FALSOS = {"no", "n", "false", "0", "-", "falso"}
_FORMATOS_FECHA = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def leer_csv(ruta, encoding="utf-8-sig", delimiter=None):
    """
    Lee un CSV a una lista de dicts con claves normalizadas (minúsculas, sin
    espacios ni BOM). Autodetecta el separador (`,` `;` o tab) si no se indica.
    Ignora filas totalmente vacías. Excel en español suele exportar con `;`.
    """
    with open(ruta, newline="", encoding=encoding) as f:
        muestra = f.read(4096)
        f.seek(0)
        if not delimiter:
            try:
                delimiter = csv.Sniffer().sniff(muestra, delimiters=",;\t").delimiter
            except csv.Error:
                delimiter = ","
        reader = csv.DictReader(f, delimiter=delimiter)
        filas = []
        for cruda in reader:
            fila = {
                (k or "").strip().lstrip("﻿").lower(): (v or "")
                for k, v in cruda.items()
                if k is not None
            }
            if any(str(v).strip() for v in fila.values()):
                filas.append(fila)
    return filas


def parse_bool(valor, default=False):
    """Interpreta Si/No/true/1/x… Devuelve `default` si la celda está vacía."""
    if valor is None:
        return default
    v = str(valor).strip().lower()
    if v == "":
        return default
    if v in _VERDADEROS:
        return True
    if v in _FALSOS:
        return False
    return default


def parse_fecha(valor, campo):
    """Convierte una fecha en YYYY-MM-DD (o dd/mm/aaaa). Lanza ValueError si no puede."""
    raw = (valor or "").strip()
    for fmt in _FORMATOS_FECHA:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{campo}: fecha inválida '{raw}' (use formato YYYY-MM-DD)")


def generar_password(longitud=12):
    """Contraseña aleatoria legible con al menos una minúscula, mayúscula y dígito."""
    alfabeto = string.ascii_letters + string.digits + "!@#$%*?"
    while True:
        pwd = "".join(secrets.choice(alfabeto) for _ in range(longitud))
        if (any(c.islower() for c in pwd)
                and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd)):
            return pwd
