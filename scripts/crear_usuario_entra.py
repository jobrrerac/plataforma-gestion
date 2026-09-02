#!/usr/bin/env python3
"""
Alta de usuarios en Entra ID para la plataforma de gestión de recursos.

Crea la cuenta en Entra ID con contraseña temporal y cambio obligatorio en el
primer inicio de sesión, y le asigna el app role que corresponda. Todo el acceso
a la plataforma es por SSO: la cuenta de Entra es la que importa.

Uso:
    python scripts/crear_usuario_entra.py daniel.guzman@inetum.com
    python scripts/crear_usuario_entra.py ana.perez@inetum.com --rol PM
    python scripts/crear_usuario_entra.py a@inetum.com b@inetum.com --simular

Tambien cambia el rol de quien ya existe:

    python scripts/crear_usuario_entra.py erika...@inetum.com --rol PM --solo-rol

IMPORTANTE: a quien se cree con este script hay que anadirlo tambien a
`roles_entra` en terraform/variables.tf, en el mismo cambio. Si no, Terraform no
sabe que existe y el fichero que se documenta como el unico punto de control de
acceso deja de serlo. Ya paso con daniel.guzman: 23 asignaciones en el estado y
24 en Entra, y hubo que arreglarlo con `terraform import`.
Ver docs/DISENO_ACCESO.md.

En Entra una asignacion de app role no se edita: se quita y se crea otra. Y
quien haya sido invitado por B2B tiene DOS identidades —su cuenta local y el
objeto de invitado—, cada una con su propia asignacion. Cambiar el rol en una
sola deja el rol efectivo a merced de por cual entre, sin ningun aviso. El
script las trata juntas.

Por qué existe: hasta ahora cada alta se hacía a mano por el portal, que son
cinco pantallas y dos pasos fáciles de olvidar (el app role y el aviso de
cambio de contraseña). Sin el app role la persona ve "se necesita aprobación
del administrador" y no entra: la aplicación exige asignación explícita.
"""

import argparse
import csv
import json
import secrets
import shutil
import string
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Constantes del entorno. Coinciden con terraform/ y con las variables de
# entorno de la Container App (OIDC_RP_CLIENT_ID, OIDC_DOMINIO_ALIAS).
# ---------------------------------------------------------------------------
TENANT_ESPERADO = "fdb323c6-1c3c-47a4-9144-2cabbc82699c"  # inetumoffshore.onmicrosoft.com
APP_ID = "d47ef129-5910-49e1-be94-36f20be7b7f5"  # Plataforma Gestion de Recursos (prod)

# El tenant no tiene verificado el dominio corporativo, así que los UPN de Entra
# van en onmicrosoft.com. La aplicación traduce el dominio al entrar
# (OIDC_DOMINIO_ALIAS), y por eso la parte local tiene que coincidir EXACTAMENTE
# con la del correo corporativo: es lo único que enlaza la cuenta de Entra con
# el usuario que ya existe en la plataforma. Si no coincide, el primer login
# crea un usuario nuevo y vacío, sin ningún error visible.
DOMINIO_CORPORATIVO = "inetum.com"
DOMINIO_ENTRA = "inetumoffshore.onmicrosoft.com"

URL_APLICACION = (
    "https://ca-platgestion-prod-eus2-001.redocean-b9f4e1e1.eastus2.azurecontainerapps.io/"
)

PLANTILLA_RECURSOS = RAIZ / "docs" / "plantillas" / "recursos.csv"
SALIDA_CREDENCIALES = RAIZ / "credenciales_entra.csv"  # cubierto por .gitignore: credenciales*.csv

AZ = shutil.which("az") or shutil.which("az.cmd") or "az"


class Fallo(Exception):
    """Error esperado: se muestra limpio, sin traza."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def az(*args, entrada_json=None):
    """Ejecuta `az` y devuelve la salida como JSON (o None si no la hay)."""
    cmd = [AZ, *args]
    if entrada_json is not None:
        cmd += ["--body", json.dumps(entrada_json)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise Fallo(f"falló `az {' '.join(args[:3])}...`:\n{proc.stderr.strip()}")
    salida = (proc.stdout or "").strip()
    return json.loads(salida) if salida else None


def generar_password(longitud=16):
    """Contraseña temporal que cumple la política de Entra (3 de 4 familias).

    Se excluyen comillas, barras y comas: la contraseña viaja por línea de
    comandos y acaba en un CSV, y esos caracteres rompen ambas cosas.
    """
    simbolos = "!#%&*+-=?@_"
    familias = [string.ascii_lowercase, string.ascii_uppercase, string.digits, simbolos]
    alfabeto = "".join(familias)
    while True:
        clave = [secrets.choice(f) for f in familias]
        clave += [secrets.choice(alfabeto) for _ in range(longitud - len(familias))]
        secrets.SystemRandom().shuffle(clave)
        candidata = "".join(clave)
        # Comprobación explícita en vez de confiar en el barajado.
        if all(any(c in f for c in candidata) for f in familias):
            return candidata


# ---------------------------------------------------------------------------
# Guardas
# ---------------------------------------------------------------------------
def verificar_tenant():
    """No crear identidades en el directorio equivocado.

    La cuenta tiene varias suscripciones repartidas en tres tenants. Crear un
    usuario en el directorio que no es no da error: da una cuenta huérfana en
    una organización ajena. Mismo criterio que terraform/guard.tf.
    """
    ctx = az("account", "show", "-o", "json")
    if ctx["tenantId"] != TENANT_ESPERADO:
        raise Fallo(
            f"credenciales apuntando al tenant {ctx['tenantId']}, se esperaba {TENANT_ESPERADO}\n"
            f"  suscripción activa: {ctx.get('name')}\n"
            f"  corrige con: az login --tenant {DOMINIO_ENTRA}"
        )
    return ctx


def roles_de_la_app():
    """Lee los app roles del registro en vivo, no de una lista escrita a mano.

    Si algún día se añade o renombra un rol en Entra, este script lo sigue sin
    tocar nada; y un rol mal escrito se detecta antes de crear la cuenta.
    """
    app = az("ad", "app", "show", "--id", APP_ID, "-o", "json")
    roles = {r["value"]: r["id"] for r in app.get("appRoles", []) if r.get("isEnabled", True)}
    if not roles:
        raise Fallo(f"el registro {APP_ID} no tiene app roles definidos")
    return roles


# ---------------------------------------------------------------------------
# Datos de la persona
# ---------------------------------------------------------------------------
def ficha_en_plantilla(email):
    """Nombre y rol desde docs/plantillas/recursos.csv, que es la fuente de la
    carga inicial. Evita teclear el nombre a mano y que quede distinto."""
    if not PLANTILLA_RECURSOS.exists():
        return None
    with PLANTILLA_RECURSOS.open(encoding="utf-8-sig", newline="") as fh:
        for fila in csv.DictReader(fh):
            if (fila.get("email") or "").strip().lower() == email:
                return {
                    "nombre": (fila.get("nombre") or "").strip(),
                    "rol": (fila.get("rol") or "").strip(),
                }
    return None


def normalizar_email(valor):
    email = valor.strip().lower()
    if "@" not in email:
        email = f"{email}@{DOMINIO_CORPORATIVO}"
    local, _, dominio = email.partition("@")
    if dominio not in (DOMINIO_CORPORATIVO, DOMINIO_ENTRA):
        raise Fallo(f"{valor}: dominio no reconocido ({dominio})")
    if not local:
        raise Fallo(f"{valor}: falta la parte local del correo")
    return local, f"{local}@{DOMINIO_CORPORATIVO}", f"{local}@{DOMINIO_ENTRA}"


# ---------------------------------------------------------------------------
# Operaciones en Entra
# ---------------------------------------------------------------------------
def buscar_usuario(upn):
    filtro = f"userPrincipalName eq '{upn}'"
    encontrados = az("ad", "user", "list", "--filter", filtro, "-o", "json")
    return encontrados[0] if encontrados else None


def crear_usuario(upn, nombre_visible, alias, password, intentos=5):
    """Crea la cuenta, reintentando con otra contrasena si Entra la rechaza.

    Entra no solo exige complejidad: tambien rechaza contrasenas que contengan
    fragmentos del nombre o del UPN. Con nombres largos eso pasa por puro azar
    —tres de diez altas fallaron asi la primera vez—, y como la contrasena es
    aleatoria, generar otra lo resuelve. Fallar el alta entera por eso obligaria
    a repetir el comando a mano.
    """
    ultimo_error = None
    for intento in range(intentos):
        try:
            return az(
                "ad", "user", "create",
                "--display-name", nombre_visible,
                "--user-principal-name", upn,
                "--mail-nickname", alias,
                "--password", password,
                "--force-change-password-next-sign-in", "true",
                "-o", "json",
            ), password
        except Fallo as e:
            if "password" not in str(e).lower():
                raise
            ultimo_error = e
            password = generar_password()
            if intento == 0:
                print("    aviso          Entra rechazo la contrasena; probando con otra")
        raise Fallo(
            f"{upn}: Entra rechazo {intentos} contrasenas seguidas. {ultimo_error}"
        )


def invitar_b2b(email_corporativo, nombre_visible, url_destino, avisar=True):
    """Invita a la persona como usuaria externa con su cuenta corporativa.

    Entra crea un objeto Guest cuyo UPN es una deformacion de su correo
    (`nombre_dominio#EXT#@tenant`). El rol NO se asigna aqui: lo pone Terraform
    desde `invitados_b2b`, para que el control de acceso viva en un solo sitio.

    Devuelve (objeto, ya_existia).
    """
    existente = az(
        "ad", "user", "list",
        "--filter", f"mail eq '{email_corporativo}' and userType eq 'Guest'",
        "-o", "json",
    )
    if existente:
        return existente[0], True

    creada = az(
        "rest", "--method", "POST",
        "--url", "https://graph.microsoft.com/v1.0/invitations",
        "--headers", "Content-Type=application/json",
        "-o", "json",
        entrada_json={
            "invitedUserEmailAddress": email_corporativo,
            "invitedUserDisplayName": nombre_visible,
            "inviteRedirectUrl": url_destino,
            "sendInvitationMessage": avisar,
        },
    )
    return {"id": creada["invitedUser"]["id"],
            "userPrincipalName": f"{email_corporativo} (invitacion enviada)",
            "redeem_url": creada.get("inviteRedeemUrl", "")}, False


def identidades_de(upn_entra, email_corporativo):
    """Todas las identidades de esa persona en el tenant.

    Puede tener dos: la cuenta local que se le creó (`@onmicrosoft`) y, si se la
    invitó por B2B, un objeto de invitado cuyo `mail` es su correo corporativo.

    Hay que tratarlas juntas. El rol vive en cada objeto por separado, así que
    cambiarlo en una sola deja el rol efectivo a merced de por cuál entre —y no
    hay nada en pantalla que avise de la discrepancia.
    """
    vistos, encontradas = set(), []
    for filtro in (
        f"userPrincipalName eq '{upn_entra}'",
        f"mail eq '{email_corporativo}'",
    ):
        for u in az("ad", "user", "list", "--filter", filtro, "-o", "json") or []:
            if u["id"] not in vistos:
                vistos.add(u["id"])
                encontradas.append(u)
    return encontradas


def reconciliar_rol(sp_id, usuario_id, rol_id):
    """Deja a esa identidad exactamente con el rol pedido, y con ninguno más.

    En Entra una asignación de app role **no se edita**: se quita y se crea otra.
    Por eso esto retira las que sobran en vez de limitarse a añadir la que falta;
    si no, alguien acabaría con Ingeniero y PM a la vez y el token traería los
    dos, que es justo lo que la sincronización de grupos no sabe resolver.

    El app role tampoco es opcional: el registro exige asignación explícita, así
    que sin ninguna la persona ve "se necesita aprobación del administrador".
    """
    asignaciones = az(
        "rest", "--method", "GET",
        "--url", f"https://graph.microsoft.com/v1.0/users/{usuario_id}/appRoleAssignments",
        "-o", "json",
    )
    ya_estaba, retiradas = False, []
    for a in (asignaciones or {}).get("value", []):
        if a.get("resourceId") != sp_id:
            continue  # otra aplicación, no es asunto nuestro
        if a.get("appRoleId") == rol_id:
            ya_estaba = True
            continue
        az(
            "rest", "--method", "DELETE",
            "--url",
            f"https://graph.microsoft.com/v1.0/users/{usuario_id}"
            f"/appRoleAssignments/{a['id']}",
            "-o", "json",
        )
        retiradas.append(a.get("appRoleId"))

    if not ya_estaba:
        az(
            "rest", "--method", "POST",
            "--url", f"https://graph.microsoft.com/v1.0/users/{usuario_id}/appRoleAssignments",
            "--headers", "Content-Type=application/json",
            "-o", "json",
            entrada_json={"principalId": usuario_id, "resourceId": sp_id, "appRoleId": rol_id},
        )
    return ya_estaba, retiradas


def registrar_credencial(upn, email_corporativo, rol, password):
    nuevo = not SALIDA_CREDENCIALES.exists()
    with SALIDA_CREDENCIALES.open("a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if nuevo:
            w.writerow(["upn_entra", "email_plataforma", "rol", "password_temporal"])
        w.writerow([upn, email_corporativo, rol, password])


# ---------------------------------------------------------------------------
def procesar(entrada, rol_forzado, roles, sp_id, simular, solo_rol=False, sin_rol=False, invitar=False):
    alias, email_corporativo, upn = normalizar_email(entrada)
    ficha = ficha_en_plantilla(email_corporativo) or {}

    # Con --sin-rol el app role lo pone Terraform, así que aquí da igual cuál
    # sea. Exigirlo creaba un círculo imposible al estrenar un rol nuevo: sin la
    # identidad en el tenant Terraform no puede planear —`data azuread_user`
    # falla— y sin el rol ya creado en Entra este script se negaba a crear la
    # identidad. --sin-rol es exactamente por dónde se rompe ese círculo.
    rol = rol_forzado or ficha.get("rol")
    if not sin_rol:
        if not rol:
            raise Fallo(
                f"{email_corporativo}: no está en {PLANTILLA_RECURSOS.name} y no se indicó --rol.\n"
                f"  roles disponibles: {', '.join(sorted(roles))}"
            )
        if rol not in roles:
            raise Fallo(f"{email_corporativo}: rol '{rol}' no existe. Disponibles: {', '.join(sorted(roles))}")

    nombre_visible = ficha.get("nombre") or alias

    if invitar:
        if simular:
            print("")
            print(f"  {email_corporativo}")
            print(f"    nombre         {nombre_visible}")
            print("    accion         se invitaria por B2B (simulacro)")
            return None
        objeto, ya_estaba = invitar_b2b(email_corporativo, nombre_visible, URL_APLICACION)
        print("")
        print(f"  {email_corporativo}")
        print(f"    invitacion     {'ya existia' if ya_estaba else 'enviada por correo'}")
        print("    app role       lo asigna Terraform (invitados_b2b)")
        return None

    identidades = identidades_de(upn, email_corporativo)

    print("")
    print(f"  {email_corporativo}")
    print(f"    nombre         {nombre_visible}")
    print(f"    rol            {rol}")
    for u in identidades:
        print(f"    identidad      {u['userPrincipalName']}")

    if simular:
        if identidades:
            print(f"    accion         se ajustaría el rol en {len(identidades)} identidad(es) (simulacro)")
        else:
            print("    accion         se crearía la cuenta (simulacro)")
        return None

    password = None
    if not identidades:
        if solo_rol:
            raise Fallo(f"{email_corporativo}: no existe en el tenant y se pidió --solo-rol.")
        password = generar_password()
        creado, password = crear_usuario(upn, nombre_visible, alias, password)
        identidades = [creado]
        print("    cuenta         creada, con cambio de contraseña obligatorio en el primer acceso")
    else:
        print("    cuenta         ya existía, no se toca (ni la contraseña)")

    if sin_rol:
        # El rol lo pone Terraform desde `roles_entra`, que es donde vive el
        # control de acceso. Que lo asignara tambien el script es lo que dejo a
        # daniel.guzman con acceso fuera del estado de Terraform.
        print("    app role       lo asigna Terraform (--sin-rol)")
        if password:
            registrar_credencial(upn, email_corporativo, rol, password)
            return {"upn": upn, "password": password, "rol": rol}
        return None

    nombres_rol = {v: k for k, v in roles.items()}
    for u in identidades:
        ya, retiradas = reconciliar_rol(sp_id, u["id"], roles[rol])
        etiqueta = u["userPrincipalName"]
        print(f"    identidad      {etiqueta}")
        if retiradas:
            quitados = ", ".join(nombres_rol.get(r, r) for r in retiradas)
            print(f"      app role     {rol}  (retirado: {quitados})")
        elif ya:
            print(f"      app role     ya tenía {rol}")
        else:
            print(f"      app role     {rol} asignado")

    if password:
        registrar_credencial(upn, email_corporativo, rol, password)
        return {"upn": upn, "password": password, "rol": rol}
    return None


def main():
    p = argparse.ArgumentParser(
        description="Da de alta usuarios en Entra ID para la plataforma (SSO).",
        epilog="Las contraseñas temporales se anexan a credenciales_entra.csv (ignorado por git).",
    )
    p.add_argument("emails", nargs="+", help="correo corporativo, p. ej. daniel.guzman@inetum.com")
    p.add_argument("--rol", help="Admin | PM | Ingeniero. Por defecto, el de docs/plantillas/recursos.csv")
    p.add_argument("--simular", action="store_true", help="muestra lo que haría sin tocar nada")
    p.add_argument(
        "--solo-rol", action="store_true", dest="solo_rol",
        help="solo ajusta el rol de quien ya existe; no da de alta a nadie",
    )
    p.add_argument(
        "--sin-rol", action="store_true", dest="sin_rol",
        help="crea la identidad y NO toca el app role; lo pone Terraform",
    )
    p.add_argument(
        "--invitar", action="store_true",
        help="invita por B2B con la cuenta corporativa, en vez de crear cuenta local",
    )
    args = p.parse_args()

    try:
        ctx = verificar_tenant()
        print(f"tenant   {ctx['tenantId']}  ({ctx.get('user', {}).get('name')})")
        roles = roles_de_la_app()
        sp_id = az("ad", "sp", "show", "--id", APP_ID, "--query", "id", "-o", "json")
        print(f"registro {APP_ID}  roles: {', '.join(sorted(roles))}")

        credenciales, errores = [], []
        for entrada in args.emails:
            try:
                r = procesar(entrada, args.rol, roles, sp_id, args.simular, args.solo_rol,
                             args.sin_rol, args.invitar)
                if r:
                    credenciales.append(r)
            except Fallo as e:
                errores.append(str(e))
                print(f"    ERROR          {e}")

        if credenciales:
            print(f"\nContraseñas temporales (también en {SALIDA_CREDENCIALES.name}):\n")
            for c in credenciales:
                print(f"  {c['upn']}")
                print(f"    contraseña   {c['password']}")
                print("    la cambia al entrar por primera vez\n")

        if errores:
            print(f"\n{len(errores)} con error.", file=sys.stderr)
            return 1
        return 0
    except Fallo as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # La consola de Windows por defecto es cp1252 y se come los acentos.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
