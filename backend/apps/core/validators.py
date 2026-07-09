"""
Máscaras de formato SAP para los códigos del proyecto.

Formatos de referencia (jerarquía 1:1:1):
  - Código proyecto: V-00869252/D  → letra + "-" + 8 dígitos + "/" + letra
  - Código PEP:      L-00869252/A  → letra + "-" + 8 dígitos + "/" + letra
  - Grafo:           2000269630    → 10 dígitos

La validación es OPCIONAL por defecto (los códigos legados como "QA-001"
siguen siendo válidos). Para exigir consistencia antes de guardar, poner
SAP_VALIDACION_ESTRICTA=True en el .env: a partir de ahí el admin, la API
y el loader rechazan valores que no cumplan la máscara.
"""
import re

from django.conf import settings
from django.core.exceptions import ValidationError

REGEX_CODIGO_PROYECTO = re.compile(r"^[A-Z]-\d{8}/[A-Z]$")
REGEX_CODIGO_PEP = re.compile(r"^[A-Z]-\d{8}/[A-Z]$")
REGEX_GRAFO = re.compile(r"^\d{10}$")

FORMATO_CODIGO_PROYECTO = "letra-8 dígitos/letra (ej: V-00869252/D)"
FORMATO_CODIGO_PEP = "letra-8 dígitos/letra (ej: L-00869252/A)"
FORMATO_GRAFO = "10 dígitos (ej: 2000269630)"


def _estricto() -> bool:
    return getattr(settings, "SAP_VALIDACION_ESTRICTA", False)


def cumple_formato_proyecto(valor) -> bool:
    return bool(valor) and bool(REGEX_CODIGO_PROYECTO.match(valor))


def cumple_formato_pep(valor) -> bool:
    return bool(valor) and bool(REGEX_CODIGO_PEP.match(valor))


def cumple_formato_grafo(valor) -> bool:
    return bool(valor) and bool(REGEX_GRAFO.match(valor))


def validar_codigo_proyecto(valor):
    """Validador de campo: solo bloquea en modo estricto; vacío siempre pasa."""
    if valor and _estricto() and not cumple_formato_proyecto(valor):
        raise ValidationError(f"Código de proyecto inválido. Formato esperado: {FORMATO_CODIGO_PROYECTO}.")


def validar_codigo_pep(valor):
    if valor and _estricto() and not cumple_formato_pep(valor):
        raise ValidationError(f"Código PEP inválido. Formato esperado: {FORMATO_CODIGO_PEP}.")


def validar_grafo(valor):
    if valor and _estricto() and not cumple_formato_grafo(valor):
        raise ValidationError(f"Grafo inválido. Formato esperado: {FORMATO_GRAFO}.")


def avisos_formato_sap(codigo=None, codigo_pep=None, grafo=None) -> list[str]:
    """
    Lista de avisos de formato para los valores informados (ignora vacíos).
    Pensado para el modo NO estricto: permite reportar inconsistencias
    (p. ej. en la carga masiva) sin bloquear el guardado.
    """
    avisos = []
    if codigo and not cumple_formato_proyecto(codigo):
        avisos.append(f"codigo '{codigo}' no cumple el formato SAP {FORMATO_CODIGO_PROYECTO}")
    if codigo_pep and not cumple_formato_pep(codigo_pep):
        avisos.append(f"codigo_pep '{codigo_pep}' no cumple el formato SAP {FORMATO_CODIGO_PEP}")
    if grafo and not cumple_formato_grafo(grafo):
        avisos.append(f"grafo '{grafo}' no cumple el formato SAP {FORMATO_GRAFO}")
    return avisos
