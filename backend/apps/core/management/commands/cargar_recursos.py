"""
Carga masiva (upsert) de usuarios/recursos desde un CSV.

Cada fila = una persona con un rol (Admin, PM o Ingeniero). Comportamiento:
  - User (login): se busca por `username`. Si existe se actualiza; si no, se crea
    con contraseña aleatoria (reportada) o la fija de --password.
  - Recurso: se crea para Ingenieros (o cualquier fila con `banda`). Se busca por
    `nro_persona_sap` y, en su defecto, por `email`.
  - Skills, clusters y tarifa (append-only) se aplican para el recurso.

Reejecutar es seguro: lo existente se actualiza, no se duplica.

Uso:
  docker compose exec web python manage.py cargar_recursos recursos.csv
  docker compose exec web python manage.py cargar_recursos recursos.csv --dry-run
  docker compose exec web python manage.py cargar_recursos recursos.csv --reemplazar-skills
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User, Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.carga_utils import leer_csv, parse_bool, parse_fecha, generar_password
from apps.core.models import Recurso, RecursoSkill, Skill, Cluster, TarifaVigente

ROLES = {"ADMIN": "Admin", "PM": "PM", "INGENIERO": "Ingeniero"}
BANDAS = {"JR", "SSR", "SR", "LEAD"}
# Admin y PM operan en /admin/; el Ingeniero usa el dashboard (no requiere staff).
STAFF_POR_DEFECTO = {"Admin": True, "PM": True, "Ingeniero": False}


class _Rollback(Exception):
    """Señal interna para deshacer todo al final de un --dry-run."""


class Command(BaseCommand):
    help = "Carga/actualiza usuarios y recursos (y tarifas de ingenieros) desde un CSV."

    def add_arguments(self, parser):
        parser.add_argument("archivo", help="Ruta del CSV a cargar.")
        parser.add_argument("--delimiter", default=None, help="Separador de columnas (autodetecta , ; o tab).")
        parser.add_argument("--encoding", default="utf-8-sig", help="Codificación del archivo (default utf-8-sig).")
        parser.add_argument("--password", default=None, help="Contraseña fija para los usuarios nuevos (por defecto: aleatoria + reporte).")
        parser.add_argument("--reporte", default=None, help="Ruta del CSV de credenciales generadas.")
        parser.add_argument("--reemplazar-skills", action="store_true", dest="reemplazar_skills",
                            help="Deja en cada recurso EXACTAMENTE los skills del CSV (borra los no listados).")
        parser.add_argument("--orden-nombre", dest="orden_nombre",
                            choices=["nombre-apellido", "apellido-nombre"], default="nombre-apellido",
                            help="Orden del campo 'nombre': 'nombre-apellido' (default) o "
                                 "'apellido-nombre' (formato SAP 'Apellidos Nombres').")
        parser.add_argument("--dry-run", action="store_true", help="Simula sin escribir en la base de datos.")

    def handle(self, *args, **o):
        filas = leer_csv(o["archivo"], o["encoding"], o["delimiter"])
        if not filas:
            raise CommandError("El archivo no tiene filas de datos.")

        faltantes = [g for g in ROLES.values() if not Group.objects.filter(name=g).exists()]
        if faltantes:
            raise CommandError(
                f"Faltan los grupos {faltantes}. Ejecute primero: python manage.py setup_grupos"
            )

        # Duplicados DENTRO del archivo: casar por SAP/email a un registro ya
        # tocado en esta misma corrida sobrescribiría a otra persona. Se marcan
        # como error y se omiten, en vez de corromper datos silenciosamente.
        duplicados = self._duplicados_en_archivo(filas)

        creados = actualizados = 0
        errores = []
        credenciales = []

        try:
            with transaction.atomic():
                for i, fila in enumerate(filas, start=2):  # línea 1 = encabezado
                    if i in duplicados:
                        errores.append((i, fila.get("username") or fila.get("email") or "?", duplicados[i]))
                        continue
                    try:
                        with transaction.atomic():
                            accion, cred = self._procesar(fila, o)
                        if accion == "creado":
                            creados += 1
                        else:
                            actualizados += 1
                        if cred:
                            credenciales.append(cred)
                    except Exception as e:  # noqa: BLE001 — aislar fila y continuar
                        errores.append((i, fila.get("username") or fila.get("email") or "?", str(e)))
                if o["dry_run"]:
                    raise _Rollback()
        except _Rollback:
            self.stdout.write(self.style.WARNING("DRY-RUN: no se escribió nada."))

        if credenciales and not o["dry_run"]:
            self._escribir_credenciales(credenciales, o)

        self._resumen(creados, actualizados, errores, o["dry_run"])

    # ── validación previa ────────────────────────────────────────────────
    def _duplicados_en_archivo(self, filas):
        """Devuelve {nº_línea: motivo} para filas cuyo username/email/SAP ya
        apareció antes en el mismo archivo (la primera aparición se conserva)."""
        vistos = {"username": {}, "email": {}, "nro_persona_sap": {}}
        etiqueta = {"username": "username", "email": "email", "nro_persona_sap": "nro_persona_sap"}
        duplicados = {}
        for i, fila in enumerate(filas, start=2):
            for campo, registro in vistos.items():
                val = (fila.get(campo) or "").strip().lower()
                if not val:
                    continue
                if val in registro and i not in duplicados:
                    duplicados[i] = f"{etiqueta[campo]} '{val}' duplicado (ya aparece en la línea {registro[val]})"
                registro.setdefault(val, i)
        return duplicados

    @staticmethod
    def _partir_nombre(nombre, orden):
        """Separa el nombre completo en (nombres, apellidos) para el User de login.
        Los datos de SAP vienen como 'Apellidos Nombres' (apellido-nombre)."""
        nombre = " ".join(nombre.split())  # normaliza espacios múltiples
        if " " not in nombre:
            return nombre, ""
        if orden == "apellido-nombre":
            apellidos, _, nombres = nombre.rpartition(" ")
            return nombres, apellidos
        nombres, _, apellidos = nombre.partition(" ")  # nombre-apellido (default)
        return nombres, apellidos

    # ── procesamiento de una fila ────────────────────────────────────────
    def _procesar(self, fila, o):
        rol_raw = (fila.get("rol") or "").strip().upper()
        if rol_raw not in ROLES:
            raise ValueError(f"rol inválido '{fila.get('rol')}' (use Admin, PM o Ingeniero)")
        grupo_nombre = ROLES[rol_raw]

        username = (fila.get("username") or "").strip()
        email = (fila.get("email") or "").strip().lower()
        nombre = (fila.get("nombre") or "").strip()
        if not username:
            raise ValueError("username requerido")
        if not email:
            raise ValueError("email requerido")
        if not nombre:
            raise ValueError("nombre requerido")

        # --- User (upsert por username) ---
        cred = None
        user, creado = User.objects.get_or_create(username=username, defaults={"email": email})
        if creado:
            pwd = o["password"] or generar_password()
            user.set_password(pwd)
            if not o["password"]:
                cred = (username, email, pwd)
        nombres, apellidos = self._partir_nombre(nombre, o["orden_nombre"])
        user.first_name = nombres[:150]
        user.last_name = apellidos[:150]
        user.email = email
        user.is_staff = parse_bool(fila.get("es_staff"), STAFF_POR_DEFECTO[grupo_nombre])
        user.save()

        grupo = Group.objects.get(name=grupo_nombre)
        otros = Group.objects.filter(name__in=set(ROLES.values()) - {grupo_nombre})
        user.groups.remove(*otros)
        user.groups.add(grupo)

        # --- Recurso (Ingeniero, o cualquier fila con banda) ---
        banda = (fila.get("banda") or "").strip().upper()
        nro_sap = (fila.get("nro_persona_sap") or "").strip() or None
        if grupo_nombre == "Ingeniero" or banda:
            if not banda:
                raise ValueError("banda requerida para Ingeniero (JR/SSR/SR/LEAD)")
            if banda not in BANDAS:
                raise ValueError(f"banda inválida '{banda}' (use JR, SSR, SR o LEAD)")

            recurso = None
            if nro_sap:
                recurso = Recurso.all_objects.filter(nro_persona_sap=nro_sap).first()
            if recurso is None:
                recurso = Recurso.all_objects.filter(email=email).first()
            if recurso is None:
                recurso = Recurso(email=email)

            recurso.nombre = nombre
            recurso.email = email
            recurso.banda = banda
            recurso.nro_persona_sap = nro_sap
            recurso.activo = parse_bool(fila.get("activo"), True)
            recurso.usuario = user
            recurso.deleted_at = None  # reactivar si había sido soft-deleteado
            recurso.save()

            self._aplicar_skills(recurso, fila.get("skills"), o["reemplazar_skills"])
            self._aplicar_clusters(recurso, fila.get("clusters"))
            self._aplicar_tarifa(recurso, fila)

        return ("creado" if creado else "actualizado"), cred

    def _aplicar_skills(self, recurso, raw, reemplazar):
        raw = (raw or "").strip()
        if not raw:
            return
        skill_ids = []
        for item in raw.split(";"):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                nombre, nivel_raw = item.split(":", 1)
                try:
                    nivel = int(nivel_raw.strip())
                except ValueError:
                    raise ValueError(f"nivel de skill inválido en '{item}' (use skill:1-5)") from None
            else:
                nombre, nivel = item, 3
            nombre = nombre.strip()
            if not (1 <= nivel <= 5):
                raise ValueError(f"nivel de skill fuera de rango (1-5) en '{item}'")
            skill, _ = Skill.objects.get_or_create(nombre=nombre)
            RecursoSkill.objects.update_or_create(
                recurso=recurso, skill=skill, defaults={"suficiencia": nivel}
            )
            skill_ids.append(skill.pk)
        if reemplazar:
            RecursoSkill.objects.filter(recurso=recurso).exclude(skill_id__in=skill_ids).delete()

    def _aplicar_clusters(self, recurso, raw):
        raw = (raw or "").strip()
        if not raw:
            return
        clusters = []
        for cod in raw.split(";"):
            cod = cod.strip()
            if cod:
                cl, _ = Cluster.objects.get_or_create(codigo=cod)
                clusters.append(cl)
        recurso.clusters.set(clusters)

    def _aplicar_tarifa(self, recurso, fila):
        valor_raw = (fila.get("tarifa_valor_hora") or "").strip()
        fecha_raw = (fila.get("tarifa_fecha_desde") or "").strip()
        if not valor_raw and not fecha_raw:
            return
        if not (valor_raw and fecha_raw):
            raise ValueError("tarifa incompleta: se requieren tarifa_valor_hora y tarifa_fecha_desde juntos")
        try:
            valor = Decimal(valor_raw.replace(",", "."))
        except InvalidOperation:
            raise ValueError(f"tarifa_valor_hora inválida: '{valor_raw}'") from None
        if valor <= 0:
            raise ValueError("tarifa_valor_hora debe ser mayor a 0")
        fecha = parse_fecha(fecha_raw, "tarifa_fecha_desde")

        # Append-only: nunca se edita una tarifa existente. Una nueva vigencia
        # se registra con otra fecha_desde.
        existente = TarifaVigente.objects.filter(recurso=recurso, fecha_desde=fecha).first()
        if existente:
            if existente.valor_hora != valor:
                self.stderr.write(self.style.WARNING(
                    f"  · {recurso.nombre}: ya existe tarifa vigente desde {fecha} "
                    f"({existente.valor_hora} €/h). Append-only: no se modifica. "
                    f"Para cambiarla, use otra fecha_desde."
                ))
            return
        TarifaVigente.objects.create(recurso=recurso, valor_hora=valor, fecha_desde=fecha)

    # ── salida ───────────────────────────────────────────────────────────
    def _escribir_credenciales(self, credenciales, o):
        import csv as _csv
        ruta = o["reporte"] or f"credenciales_generadas_{datetime.now():%Y%m%d_%H%M%S}.csv"
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            w.writerow(["username", "email", "password_temporal"])
            w.writerows(credenciales)
        self.stdout.write(self.style.WARNING(
            f"⚠ {len(credenciales)} credencial(es) nueva(s) escritas en '{ruta}'. "
            f"Contiene secretos: entréguelas por canal seguro, exija cambio de clave en el primer "
            f"ingreso y borre el archivo."
        ))

    def _resumen(self, creados, actualizados, errores, dry_run):
        prefijo = "[DRY-RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefijo}Recursos — creados: {creados}, actualizados: {actualizados}, errores: {len(errores)}"
        ))
        for linea, ref, msg in errores:
            self.stderr.write(self.style.ERROR(f"  línea {linea} ({ref}): {msg}"))
