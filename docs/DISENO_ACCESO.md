# Diseño de acceso: quién entra y cómo

Cómo se da de alta a una persona hoy, y hacia dónde va esto.

---

## Hoy: alta de una persona

Hay **tres cosas distintas** que solemos meter en el mismo saco, y separarlas
evita casi todos los errores:

| | Qué es | Dónde vive |
|---|---|---|
| **Identidad** | La cuenta con la que se autentica | Entra ID |
| **Acceso** | Si puede entrar a *esta* aplicación | App role, en `terraform/variables.tf` |
| **Cuenta** | Su historial: recurso, asignaciones, horas | Base de datos de Django |

### El orden importa

**1. Primero la identidad, en Entra.** `terraform/variables.tf` **no crea
usuarios**: `data "azuread_user" "asignados"` los *busca* por UPN. Si la persona
no existe en Entra, `terraform plan` falla antes de hacer nada.

**2. Después el acceso, en `variables.tf`.** Se añade su UPN al mapa
`roles_entra` con el rol que le corresponde y se aplica.

**3. La cuenta de Django se crea sola** en su primer inicio de sesión
(`OIDC_CREAR_USUARIOS = True`), o ya existe si vino del importador masivo.

> **Con `app_role_assignment_required = true`, quien no esté en `roles_entra` no
> entra.** Ese mapa no es una lista de permisos: es la puerta.

### El script y Terraform hacen lo mismo, y eso ha costado

`scripts/crear_usuario_entra.py` crea la identidad **y** asigna el rol. Terraform
también asigna el rol. Son dos herramientas escribiendo sobre lo mismo, y ya ha
pasado dos veces:

- **Erika**: el script le asignó `Ingeniero`; al aplicar Terraform con `PM` iba a
  quedarse con **los dos roles**, porque la asignación del script no estaba en el
  estado. Hubo que retirarla a mano antes de aplicar.
- **Daniel**: creado con el script y nunca añadido a `roles_entra`. Terraform
  gestionaba 23 asignaciones y en Entra había 24. El fichero que dice ser el
  único punto de control **no lo era**. Se corrigió con `terraform import`.

**Regla mientras convivan las dos:** el script sirve para **crear la identidad**;
el rol lo pone **Terraform**. Quien use el script tiene que añadir a la persona a
`roles_entra` en el mismo cambio.

---

## Hacia dónde va: todos por B2B

### Qué se probó, y funcionó

El 31/08/2026 se invitó a Erika como usuaria externa con su cuenta corporativa:

```
externalUserState: Accepted        (canjeó a las 16:55)
último login en la aplicación:     17:12
```

Eso despeja la única incógnita que había: **el tenant de Inetum permite
colaboración B2B saliente**. No hace falta pedir nada a su IT para el acceso.

### Por qué es mejor que lo de ahora

Hoy cada persona tiene una cuenta `@inetumoffshore.onmicrosoft.com` **con su
propia contraseña**. Eso es exactamente lo que se quería evitar: una segunda
contraseña que recordar, que caduca, que se olvida y que acaba en un papel.

Con B2B se autentica contra el tenant de Inetum, con la cuenta que ya usa todos
los días. **Cero contraseñas nuevas.** Y de paso hereda su MFA corporativo sin
tener que montar ninguno aquí.

### Lo que hay que resolver

**1. Una identidad por persona, no dos.**

Es el punto que más duele. Un invitado tiene dos objetos en el tenant:

```
MIEMBRO   erika.castiblanco-monroy@inetumoffshore.onmicrosoft.com
INVITADA  erika.castiblanco-monroy_inetum.com#EXT#@inetumoffshore.onmicrosoft.com
```

**El app role vive en cada uno por separado.** Ya pasó: se le cambió el rol a PM,
Terraform dijo que todo estaba al día, y siguió entrando como Ingeniero — porque
había entrado por la identidad que Terraform no tocaba.

Se tapó con la variable `invitados_b2b`, que gestiona el rol de las dos. Pero eso
es una tirita: la solución de fondo es **borrar la cuenta miembro** cuando la
persona ya entra por B2B, y que quede una sola identidad.

**2. Terraform tiene que crear la invitación.**

El proveedor `azuread` trae el recurso `azuread_invitation`, que expone
`user_id`. Con eso, `variables.tf` pasaría a ser la única fuente también para
las altas:

```hcl
resource "azuread_invitation" "persona" {
  for_each              = var.invitados_b2b
  user_email_address    = "${each.value}@${var.dominio_corporativo}"
  redirect_url          = "https://<fqdn>/"
}

resource "azuread_app_role_assignment" "invitados" {
  principal_object_id = azuread_invitation.persona[each.value].user_id
}
```

Y `scripts/crear_usuario_entra.py` deja de crear identidades: pasa a ser solo la
herramienta de consulta y de arreglo puntual. **Se acaba la duplicidad.**

**3. Confiar en el MFA de Inetum.**

Por defecto nuestro tenant no confía en el MFA del suyo, así que puede pedirles
registrar un segundo factor **aquí**. Es un interruptor de nuestro lado —«Trust
MFA from Entra ID tenants» en los ajustes de acceso entre tenants— y hay que
activarlo antes de invitar a nadie más, o la primera impresión será justo la
fricción que se quería quitar.

**4. El login local se queda.**

No negociable, y no por costumbre: el secreto de cliente de Entra caduca, y el
día que caduque el botón de Microsoft deja de funcionar **sin avisar**. El
formulario de usuario y contraseña es la vía de entrada mientras se rota.

Con B2B esto cambia de significado: pasa de ser "la contraseña de todos" a ser
"la salida de emergencia de los Admin".

### Lo que ya está resuelto

**El UPN deformado.** Entra fabrica el UPN de un invitado deformando su correo
(`nombre_dominio#EXT#@tenant`). Como termina en el dominio del tenant, el alias
de dominio lo habría traducido a una dirección que no es de nadie: **cuenta nueva
y vacía, sin ningún error, con el historial huérfano**. La guarda está en
`apps/accounts/oidc.py` y tiene sus tests.

### El orden para migrar

1. Activar la confianza de MFA entre tenants.
2. Pasar `azuread_invitation` a Terraform e importar la invitación de Erika.
3. Invitar por tandas, empezando por quien ya tenga poco historial.
4. Verificar que cada persona entra en **su** cuenta y ve su historial —una
   consulta que cuente usuarios antes y después basta para detectar duplicados.
5. Solo entonces, borrar las cuentas `@onmicrosoft` de quien ya entra por B2B.
6. Dejar activas las de contingencia: `inetum_admin` y las de QA.

El paso 4 no es burocracia. Es el único momento en que un duplicado se detecta
barato; después hay que reconstruir a mano a quién pertenecía cada asignación.
