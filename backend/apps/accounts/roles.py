"""Helpers centralizados de roles (grupos Admin / PM / Ingeniero).

Única fuente de verdad para checks de rol: permisos DRF, vistas, serializers,
templates y admin deben consumir estas funciones en vez de consultar
`user.groups` a mano.

Regla no negociable: los costos/tarifas se muestran por allowlist (solo Admin
y PM). Un usuario sin grupo asignado NO ve costos.
"""

ADMIN = "Admin"
PM = "PM"
INGENIERO = "Ingeniero"


def es_admin(user) -> bool:
    """Admin o superusuario."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name=ADMIN).exists()


def es_admin_o_pm(user) -> bool:
    """Admin, PM o superusuario."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name__in=[ADMIN, PM]).exists()


def puede_ver_costos(user) -> bool:
    """Tarifas y costos: solo Admin y PM (el rol Ingeniero NUNCA ve costos)."""
    return es_admin_o_pm(user)


def puede_ver_datos_personales(user) -> bool:
    """Datos de contacto de los recursos (email): solo Admin y PM."""
    return es_admin_o_pm(user)
