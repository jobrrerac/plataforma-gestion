"""Helpers centralizados de roles (grupos Admin / PM / Ingeniero / Visor).

Única fuente de verdad para checks de rol: permisos DRF, vistas, serializers,
templates y admin deben consumir estas funciones en vez de consultar
`user.groups` a mano.

**Ver y hacer son dos preguntas distintas.** Durante mucho tiempo fueron la
misma, `es_admin_o_pm`, porque quien podía ver todo era exactamente quien podía
operar. El rol Visor rompe esa coincidencia: ve la operación completa y no
escribe en ninguna parte. De ahí que ahora haya dos funciones y no una:

- `puede_ver_todo` → Admin, PM y Visor. Es la puerta de las pantallas de
  lectura: dashboard del equipo, buscador, costos, datos de contacto.
- `es_admin_o_pm` → Admin y PM. Es la puerta de todo lo que escribe: solicitar,
  ceder, liberar, registrar novedades por autoridad.

Usar la primera donde tocaba la segunda le da permiso de escritura a un Visor.
Usar la segunda donde tocaba la primera le esconde media aplicación.

Regla no negociable: **el rol Ingeniero NUNCA ve costos.** El Visor sí — es un
rol de supervisión, y así se decidió al crearlo. Un usuario sin grupo asignado
tampoco ve costos.
"""

ADMIN = "Admin"
PM = "PM"
INGENIERO = "Ingeniero"
VISOR = "Visor"

# Los cuatro roles que gestiona la plataforma. Un usuario pertenece a uno.
TODOS = (ADMIN, PM, INGENIERO, VISOR)

# Quién entra al /admin/ de Django. El Visor no: su sitio es la aplicación, y
# el admin es una herramienta de escritura por definición.
STAFF_POR_ROL = {ADMIN: True, PM: True, INGENIERO: False, VISOR: False}


def _en_grupo(user, nombres) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name__in=nombres).exists()


def es_admin(user) -> bool:
    """Admin o superusuario."""
    return _en_grupo(user, [ADMIN])


def es_admin_o_pm(user) -> bool:
    """Admin, PM o superusuario. **Puerta de las acciones que escriben.**"""
    return _en_grupo(user, [ADMIN, PM])


def es_visor(user) -> bool:
    """Rol de solo lectura.

    No usa `_en_grupo`: un superusuario no es un Visor, y responder que sí haría
    que las plantillas le escondieran los botones a quien más los necesita.
    """
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name=VISOR).exists()


def puede_ver_todo(user) -> bool:
    """Ve la operación completa: el equipo entero, no solo lo suyo.

    **Puerta de las pantallas de lectura.** Un Ingeniero se ve a sí mismo; estos
    ven a todos.
    """
    return _en_grupo(user, [ADMIN, PM, VISOR])


def puede_ver_costos(user) -> bool:
    """Tarifas y costos: Admin, PM y Visor (el rol Ingeniero NUNCA ve costos)."""
    return puede_ver_todo(user)


def puede_ver_datos_personales(user) -> bool:
    """Datos de contacto de los recursos (email): Admin, PM y Visor."""
    return puede_ver_todo(user)
