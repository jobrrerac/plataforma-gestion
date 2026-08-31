"""Backend de autenticación contra Microsoft Entra ID (OpenID Connect).

Convive con el login local: `AUTHENTICATION_BACKENDS` prueba primero
`ModelBackend`, de modo que las cuentas locales (y el superusuario de
emergencia) siguen entrando aunque Entra esté caído o el secreto de cliente
haya caducado.

Lo que hace este backend, más allá de identificar a la persona:

1. Mapea los *app roles* de Entra (`Admin` / `PM` / `Ingeniero`) a los grupos
   homónimos de Django. Ese mapeo es la razón de ser del archivo: todo el RBAC
   del proyecto se apoya en `user.groups` (ver `apps/accounts/roles.py`), así
   que sin esta sincronización un usuario SSO entraría sin permisos.
2. Enlaza la cuenta con su `Recurso` por email.
3. Levanta el bloqueo de cambio de contraseña obligatorio: quien se autentica
   con su identidad corporativa no tiene ninguna contraseña local que cambiar.

Los roles se leen del `id_token`, no del endpoint `/userinfo`: Entra emite los
app roles solo en el token, y así además nos ahorramos una llamada a Graph en
cada inicio de sesión.
"""

import logging

from django.contrib.auth.models import Group, User
from django.db import transaction
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from apps.accounts.models import CambioPasswordPendiente
from apps.accounts.roles import ADMIN, INGENIERO, PM

logger = logging.getLogger(__name__)

# Grupos que este backend administra. Un rol quitado en Entra se quita también
# en Django, pero cualquier otro grupo que alguien haya asignado a mano se
# respeta: solo se tocan estos tres.
GRUPOS_GESTIONADOS = {ADMIN, PM, INGENIERO}


class EntraOIDCBackend(OIDCAuthenticationBackend):
    def get_userinfo(self, access_token, id_token, payload):
        """Usa las claims del id_token en vez de llamar a /userinfo.

        `payload` ya viene con la firma verificada contra el JWKS de Entra. Los
        app roles (claim `roles`) solo existen aquí; `/userinfo` de Graph no los
        devuelve, así que llamarlo sería una petición de red extra que además no
        traería lo que necesitamos.
        """
        return payload

    # -- identificación ----------------------------------------------------

    def _email_token(self, claims):
        """Email tal como viene en el token, en minúsculas.

        Entra no siempre manda `email`: depende de si el usuario tiene buzón. Se
        cae hacia `preferred_username` y `upn`, que en un tenant corporativo son
        el UPN y sirven igual como identificador estable.
        """
        for clave in ("email", "preferred_username", "upn"):
            valor = (claims.get(clave) or "").strip().lower()
            if "@" in valor:
                return valor
        return ""

    def _correo_de_invitado(self, valor):
        """Recupera el correo real de un invitado B2B a partir de su UPN.

        A un invitado Entra le fabrica un UPN deformando su direccion:

            erika.castiblanco-monroy@inetum.com
            -> erika.castiblanco-monroy_inetum.com#EXT#@inetumoffshore.onmicrosoft.com

        Como termina en el dominio del tenant, `OIDC_DOMINIO_ALIAS` lo tomaria
        por una cuenta local y lo traduciria a
        `erika.castiblanco-monroy_inetum.com#EXT#@inetum.com`, que no es de
        nadie: crearia un usuario nuevo y vacio sin dar ningun error, dejando
        huerfano el historial. Es el mismo modo de fallo que motivo el alias de
        dominio, entrando por la puerta de al lado.

        Normalmente el token trae el claim `email` con la direccion buena y esto
        no llega a usarse. Pero `email` esta declarado como opcional y no
        esencial, asi que no esta garantizado.
        """
        local, _, _dominio = valor.partition("@")
        if not local.endswith("#ext#"):
            return ""
        # El separador es el ULTIMO '_': el nombre puede llevar guiones bajos.
        usuario, sep, dominio = local[: -len("#ext#")].rpartition("_")
        if not sep or "." not in dominio:
            return ""
        return f"{usuario}@{dominio}"

    def _email(self, claims):
        """Email canónico: el de negocio, que es como se conoce a la persona aquí.

        El tenant de Entra no tiene verificado el dominio corporativo, así que
        los UPN son `nombre@inetumoffshore.onmicrosoft.com` mientras que en la
        plataforma —y en `Recurso.email`— la persona es `nombre@inetum.com`.
        `OIDC_DOMINIO_ALIAS` traduce entre ambos. Sin esa traducción el SSO no
        reconocería a ninguna de las cuentas existentes y crearía duplicados,
        dejando huérfano el historial de asignaciones.
        """
        email = self._email_token(claims)
        if not email:
            return ""

        # Un invitado B2B se resuelve antes del alias: su direccion real ya es
        # la corporativa, traducir el dominio del tenant la destrozaria.
        invitado = self._correo_de_invitado(email)
        if invitado:
            return invitado

        alias = self.get_settings("OIDC_DOMINIO_ALIAS", {}) or {}
        usuario, _, dominio = email.partition("@")
        destino = alias.get(dominio)
        return f"{usuario}@{destino}" if destino else email

    def _emails_candidatos(self, claims):
        """Identificadores con los que puede estar registrada la cuenta.

        Se prueban el email canónico y el literal del token: una cuenta creada
        antes de configurar el alias pudo quedar guardada con el dominio de
        Entra, y no queremos duplicarla al arreglar la configuración.
        """
        candidatos = [self._email(claims), self._email_token(claims)]
        # dict.fromkeys preserva el orden y quita repetidos cuando no hay alias.
        return [c for c in dict.fromkeys(candidatos) if c]

    def verify_claims(self, claims):
        """Sin email no hay forma de enlazar la cuenta con un `Recurso`."""
        if not self._email(claims):
            logger.warning("SSO: token sin email/upn utilizable; se rechaza el acceso.")
            return False
        return True

    def describe_user_by_claims(self, claims):
        return f"usuario con email {self._email(claims)}"

    def _alias_usuario(self, claims):
        """Username de Django al que apunta explícitamente esta identidad de Entra.

        Para cuentas cuyo UPN no deriva de un email de negocio, como la cuenta
        administrativa del tenant. Devuelve None si no hay alias configurado.
        """
        alias = self.get_settings("OIDC_USUARIO_ALIAS", {}) or {}
        if not alias:
            return None
        return alias.get(self._email_token(claims))

    def filter_users_by_claims(self, claims):
        """Busca la cuenta existente por alias explícito, email o username.

        El `username` entra en la búsqueda porque las cuentas creadas con el
        importador masivo pueden tener un username corporativo distinto del
        email; así el usuario conserva su historial en vez de estrenar cuenta.
        """
        destino = self._alias_usuario(claims)
        if destino:
            # Un alias explícito es una instrucción, no una pista: si apunta a
            # una cuenta que no existe, el acceso se deniega en vez de crear una
            # cuenta nueva con ese nombre.
            usuarios = User.objects.filter(username__iexact=destino)
            if not usuarios.exists():
                logger.error(
                    "SSO: OIDC_USUARIO_ALIAS apunta a '%s', que no existe en Django.",
                    destino,
                )
            return usuarios

        candidatos = self._emails_candidatos(claims)
        if not candidatos:
            return User.objects.none()

        for identificador in candidatos:
            usuarios = User.objects.filter(email__iexact=identificador)
            if usuarios.exists():
                return usuarios

            usuarios = User.objects.filter(username__iexact=identificador)
            if usuarios.exists():
                return usuarios

        return User.objects.none()

    # -- alta y actualización ----------------------------------------------

    def create_user(self, claims):
        destino = self._alias_usuario(claims)
        if destino:
            # Se llegó aquí porque el alias no encontró la cuenta. Crearla sería
            # inventar un superusuario a partir de un token; mejor denegar y que
            # quede el error en los logs.
            logger.error(
                "SSO: no se crea cuenta para %s; su alias apunta a '%s', que no existe.",
                self._email_token(claims),
                destino,
            )
            return None

        if not self.get_settings("OIDC_CREAR_USUARIOS", True):
            logger.warning(
                "SSO: %s no existe en Django y OIDC_CREAR_USUARIOS=False; acceso denegado.",
                self._email(claims),
            )
            return None

        email = self._email(claims)
        nombre_completo = (claims.get("name") or "").strip()
        nombres = nombre_completo.split(" ", 1)

        usuario = User.objects.create(
            username=email,
            email=email,
            first_name=nombres[0][:150] if nombres and nombres[0] else "",
            last_name=nombres[1][:150] if len(nombres) > 1 else "",
        )
        # La cuenta no tiene contraseña local: solo se entra por SSO. Esto
        # además hace que `has_usable_password()` sea False, que es la señal que
        # usa ForzarCambioPasswordMiddleware para no molestar a estos usuarios.
        usuario.set_unusable_password()
        usuario.save(update_fields=["password"])

        logger.info("SSO: cuenta creada para %s", email)
        return self.update_user(usuario, claims)

    def update_user(self, user, claims):
        email = self._email(claims)
        # Una cuenta enlazada por alias explícito conserva su identidad de
        # negocio: el alias dice "esta identidad de Entra ES esta cuenta", no
        # "cópiale los datos del token". Sin esto, entrar como
        # admin@inetumoffshore pisaría el email real del superusuario.
        enlazada_por_alias = self._alias_usuario(claims) is not None

        with transaction.atomic():
            cambios = []
            if not enlazada_por_alias:
                if user.email.lower() != email:
                    user.email = email
                    cambios.append("email")

                nombre_completo = (claims.get("name") or "").strip()
                if nombre_completo:
                    partes = nombre_completo.split(" ", 1)
                    nuevo_nombre = partes[0][:150]
                    nuevo_apellido = partes[1][:150] if len(partes) > 1 else ""
                    if user.first_name != nuevo_nombre:
                        user.first_name = nuevo_nombre
                        cambios.append("first_name")
                    if user.last_name != nuevo_apellido:
                        user.last_name = nuevo_apellido
                        cambios.append("last_name")

            roles = self._sincronizar_grupos(user, claims)

            # Admin necesita entrar al /admin/ de Django. `is_superuser` NUNCA se
            # concede desde un token: saltarse todos los permisos es una decisión
            # deliberada que se toma a mano, no algo que otorgue un claim.
            #
            # A un superusuario no se le quita `is_staff` aunque pierda el rol:
            # dejaría al administrador de la plataforma fuera de su propio
            # /admin/, y recuperarlo exigiría acceso a la base de datos.
            es_admin = ADMIN in roles
            if user.is_staff != es_admin and not (user.is_superuser and not es_admin):
                user.is_staff = es_admin
                cambios.append("is_staff")

            if cambios:
                user.save(update_fields=cambios)

            self._enlazar_recurso(user)

            # Quien entra con su identidad corporativa no tiene contraseña local
            # que cambiar; dejar la marca puesta lo encerraría en un formulario
            # que le pide una contraseña anterior que no conoce.
            CambioPasswordPendiente.objects.filter(usuario=user).delete()

        return user

    # -- helpers -----------------------------------------------------------

    def _sincronizar_grupos(self, user, claims):
        """Deja los grupos gestionados exactamente como dicen los roles de Entra."""
        roles_token = claims.get("roles") or []
        roles = {r for r in roles_token if r in GRUPOS_GESTIONADOS}

        if not roles:
            # Sin rol asignado la persona entra, pero sin permisos: la allowlist
            # de `roles.py` hace que no vea costos ni datos personales. Es el
            # fallo seguro.
            logger.warning(
                "SSO: %s inició sesión sin ningún app role asignado en Entra.",
                user.email or user.username,
            )

        actuales = set(
            user.groups.filter(name__in=GRUPOS_GESTIONADOS).values_list("name", flat=True)
        )
        if actuales == roles:
            return roles

        for nombre in roles - actuales:
            grupo, _ = Group.objects.get_or_create(name=nombre)
            user.groups.add(grupo)

        if actuales - roles:
            user.groups.remove(*Group.objects.filter(name__in=actuales - roles))

        logger.info(
            "SSO: roles de %s actualizados %s -> %s",
            user.email or user.username,
            sorted(actuales) or "[]",
            sorted(roles) or "[]",
        )
        return roles

    def _enlazar_recurso(self, user):
        """Asocia el `Recurso` que tenga el mismo email, si aún no está enlazado."""
        # Import diferido: apps.core importa modelos que a su vez tiran de este
        # módulo indirectamente a través de settings.
        from apps.core.models import Recurso

        if Recurso.objects.filter(usuario=user).exists():
            return

        recurso = Recurso.objects.filter(email__iexact=user.email, usuario__isnull=True).first()
        if recurso:
            recurso.usuario = user
            recurso.save(update_fields=["usuario"])
            logger.info("SSO: %s enlazado al recurso %s", user.email, recurso.pk)
