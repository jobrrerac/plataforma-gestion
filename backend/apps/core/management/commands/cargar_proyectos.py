"""
Carga masiva (upsert) de proyectos desde un CSV.

Cada fila = un proyecto. Se busca por `codigo` (clave natural): si existe se
actualiza, si no se crea. El `pm_username` debe existir como usuario (cargue
primero los usuarios). Reejecutar es seguro.

Uso:
  docker compose exec web python manage.py cargar_proyectos proyectos.csv
  docker compose exec web python manage.py cargar_proyectos proyectos.csv --dry-run
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.carga_utils import leer_csv, parse_fecha
from apps.core.models import Proyecto

ESTADOS = {"ACTIVO", "EN_PAUSA", "CERRADO"}


class _Rollback(Exception):
    """Señal interna para deshacer todo al final de un --dry-run."""


class Command(BaseCommand):
    help = "Carga/actualiza proyectos (con código PEP) desde un CSV."

    def add_arguments(self, parser):
        parser.add_argument("archivo", help="Ruta del CSV a cargar.")
        parser.add_argument("--delimiter", default=None, help="Separador de columnas (autodetecta , ; o tab).")
        parser.add_argument("--encoding", default="utf-8-sig", help="Codificación del archivo (default utf-8-sig).")
        parser.add_argument("--dry-run", action="store_true", help="Simula sin escribir en la base de datos.")

    def handle(self, *args, **o):
        filas = leer_csv(o["archivo"], o["encoding"], o["delimiter"])
        if not filas:
            raise CommandError("El archivo no tiene filas de datos.")

        creados = actualizados = 0
        errores = []

        try:
            with transaction.atomic():
                for i, fila in enumerate(filas, start=2):
                    try:
                        with transaction.atomic():
                            accion = self._procesar(fila)
                        if accion == "creado":
                            creados += 1
                        else:
                            actualizados += 1
                    except Exception as e:  # noqa: BLE001 — aislar fila y continuar
                        errores.append((i, fila.get("codigo") or "?", str(e)))
                if o["dry_run"]:
                    raise _Rollback()
        except _Rollback:
            self.stdout.write(self.style.WARNING("DRY-RUN: no se escribió nada."))

        prefijo = "[DRY-RUN] " if o["dry_run"] else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefijo}Proyectos — creados: {creados}, actualizados: {actualizados}, errores: {len(errores)}"
        ))
        for linea, ref, msg in errores:
            self.stderr.write(self.style.ERROR(f"  línea {linea} ({ref}): {msg}"))

    def _procesar(self, fila):
        codigo = (fila.get("codigo") or "").strip()
        if not codigo:
            raise ValueError("codigo requerido")
        nombre = (fila.get("nombre") or "").strip()
        cliente = (fila.get("cliente") or "").strip()
        if not nombre:
            raise ValueError("nombre requerido")
        if not cliente:
            raise ValueError("cliente requerido")

        fecha_inicio = parse_fecha(fila.get("fecha_inicio"), "fecha_inicio")
        fecha_fin_raw = (fila.get("fecha_fin") or "").strip()
        fecha_fin = parse_fecha(fecha_fin_raw, "fecha_fin") if fecha_fin_raw else None
        if fecha_fin and fecha_fin < fecha_inicio:
            raise ValueError("fecha_fin no puede ser anterior a fecha_inicio")

        estado = (fila.get("estado") or "ACTIVO").strip().upper()
        if estado not in ESTADOS:
            raise ValueError(f"estado inválido '{estado}' (use ACTIVO, EN_PAUSA o CERRADO)")

        pm_username = (fila.get("pm_username") or "").strip()
        if not pm_username:
            raise ValueError("pm_username requerido")
        try:
            pm = User.objects.get(username=pm_username)
        except User.DoesNotExist:
            raise ValueError(f"pm_username '{pm_username}' no existe (cargue primero los usuarios)")
        if not (pm.is_superuser or pm.groups.filter(name__in=["Admin", "PM"]).exists()):
            self.stderr.write(self.style.WARNING(
                f"  · aviso: '{pm_username}' no pertenece a Admin/PM; se asigna igual."
            ))

        codigo_pep = (fila.get("codigo_pep") or "").strip() or None

        proyecto = Proyecto.all_objects.filter(codigo=codigo).first()
        creado = proyecto is None
        if proyecto is None:
            proyecto = Proyecto(codigo=codigo)
        proyecto.codigo_pep = codigo_pep
        proyecto.nombre = nombre
        proyecto.cliente = cliente
        proyecto.fecha_inicio = fecha_inicio
        proyecto.fecha_fin = fecha_fin
        proyecto.estado = estado
        proyecto.pm = pm
        proyecto.deleted_at = None  # reactivar si había sido soft-deleteado
        proyecto.save()

        return "creado" if creado else "actualizado"
