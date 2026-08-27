"""Prepara y retira el fixture de la ronda de pruebas QA.

Existe para que "luego borramos todo" sea un comando y no una lista de cosas
que alguien tiene que acordarse de deshacer a mano. El fixture vive dentro de
la base real, asi que dejarlo a medias ensucia la planificacion de verdad:
suma ocupacion en el dashboard y mete proyectos inventados en los desplegables.

    python manage.py datos_qa preparar
    python manage.py datos_qa limpiar

Todo lo que toca lleva el prefijo QA- o la cuenta qa.*, asi que la limpieza no
puede llevarse por delante datos reales.
"""

from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso

# Dias hacia atras que cubre la asignacion de pruebas. Tiene que caber dentro
# de la ventana que admite la legalizacion de horas (30 dias), o quien pruebe
# no podra registrar nada contra ella.
DIAS_VENTANA = 20

PREFIJO_PROYECTO = "QA-"
PREFIJO_CUENTA = "qa."


class Command(BaseCommand):
    help = "Prepara (o retira) los datos de la ronda de pruebas QA."

    def add_arguments(self, parser):
        parser.add_argument("accion", choices=["preparar", "limpiar"])

    def handle(self, *args, **options):
        if options["accion"] == "preparar":
            self._preparar()
        else:
            self._limpiar()

    # -- preparar ----------------------------------------------------------

    @transaction.atomic
    def _preparar(self):
        qa_pm = self._cuenta("qa.pm@inetum.com", "QA Pruebas (PM)", "PM")
        carmen = User.objects.filter(username__startswith="carmen.leon").first()

        # Tres proyectos con papeles distintos. Reviven los que ya existian en
        # vez de crear otros nuevos.
        proyectos = [
            ("QA-001", "Portal de Clientes QA", qa_pm, "ACTIVO", True),
            # PM distinto a proposito: es la unica forma de comprobar que la
            # cola de aprobacion filtra de verdad por proyecto.
            ("QA-002", "Migracion SAP QA", carmen or qa_pm, "ACTIVO", True),
            # Cerrado: sirve para comprobar que no aparece donde no debe.
            ("QA-003", "Mantenimiento Legacy QA", qa_pm, "CERRADO", True),
        ]

        creados = {}
        for codigo, nombre, pm, estado, facturable in proyectos:
            proyecto = Proyecto.all_objects.filter(codigo=codigo).first()
            if proyecto is None:
                proyecto = Proyecto(codigo=codigo, fecha_inicio=date.today() - timedelta(days=90))
            proyecto.nombre = nombre
            proyecto.cliente = "QA (datos de prueba)"
            proyecto.pm = pm
            proyecto.estado = estado
            proyecto.facturable = facturable
            proyecto.deleted_at = None  # revive los que se habian retirado
            proyecto.save()
            creados[codigo] = proyecto
            self.stdout.write(self.style.SUCCESS(f"  ✓ {codigo} — PM {pm.username} — {estado}"))

        # Recurso para el PM de pruebas: sin el no puede legalizar sus propias
        # horas, y ese caso esta en el plan.
        recurso_pm = self._recurso_para(qa_pm, "QA Pruebas (PM)")

        erika = Recurso.objects.filter(email__istartswith="erika.castiblanco").first()
        if erika is None:
            self.stdout.write(self.style.WARNING("  ! No se encontro el recurso de Erika"))
            return

        inicio = date.today() - timedelta(days=DIAS_VENTANA)
        for recurso in (erika, recurso_pm):
            self._asignar(recurso, creados["QA-001"], inicio, date.today(), qa_pm)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nListo. La asignacion cubre del {inicio} a hoy: dentro de ese rango "
                f"QA-001 aparece en /horas/, y fuera desaparece."
            )
        )
        self.stdout.write("Para retirarlo todo:  python manage.py datos_qa limpiar")

    def _cuenta(self, username, nombre, grupo):
        usuario, creado = User.objects.get_or_create(
            username=username,
            defaults={"email": username, "first_name": "QA", "last_name": nombre},
        )
        if creado:
            # Entra por SSO; no debe tener contrasena local utilizable.
            usuario.set_unusable_password()
            usuario.save(update_fields=["password"])
        usuario.groups.add(Group.objects.get(name=grupo))
        return usuario

    def _recurso_para(self, usuario, nombre):
        recurso, _ = Recurso.all_objects.get_or_create(
            email=usuario.email,
            defaults={"nombre": nombre, "banda": "SR"},
        )
        recurso.usuario = usuario
        recurso.deleted_at = None
        # Inactivo: es una cuenta de prueba y no debe contarse como plantilla
        # asignable en el dashboard. La legalizacion funciona igual.
        recurso.activo = False
        recurso.save()
        return recurso

    def _asignar(self, recurso, proyecto, inicio, fin, actor):
        from apps.assignments.services import calcular_horas_jornada_completa

        existente = Asignacion.objects.filter(recurso=recurso, proyecto=proyecto).first()
        if existente:
            self.stdout.write(f"  · {recurso.nombre} ya estaba asignado a {proyecto.codigo}")
            return existente

        horas = calcular_horas_jornada_completa(inicio, fin, recurso)
        asignacion = Asignacion.objects.create(
            recurso=recurso, proyecto=proyecto,
            fecha_inicio=inicio, fecha_fin=fin,
            horas_totales=horas, intensidad_diaria=8.5,
            jornada_completa=True, estado="APROBADA", solicitada_por=actor,
        )
        self.stdout.write(
            self.style.SUCCESS(f"  ✓ {recurso.nombre} → {proyecto.codigo} ({horas} h)")
        )
        return asignacion

    # -- limpiar -----------------------------------------------------------

    @transaction.atomic
    def _limpiar(self):
        """Retira el fixture. Todo por soft-delete: nada se borra de verdad."""
        proyectos = Proyecto.objects.filter(codigo__startswith=PREFIJO_PROYECTO)
        cuentas = User.objects.filter(username__startswith=PREFIJO_CUENTA)

        # Las horas legalizadas contra proyectos de prueba se retiran tambien:
        # dejarlas falsearia el informe de facturables.
        from apps.legalizacion.models import DiaLegalizado, RegistroHoras

        registros = RegistroHoras.objects.filter(proyecto__in=proyectos)
        dias_tocados = set(registros.values_list("dia_id", flat=True))
        for registro in registros:
            registro.delete()
        self.stdout.write(f"  – {len(dias_tocados)} dia(s) con horas de proyectos QA retirados")

        for dia in DiaLegalizado.objects.filter(pk__in=dias_tocados, registros__isnull=True):
            dia.delete()

        for asignacion in Asignacion.objects.filter(proyecto__in=proyectos):
            asignacion.delete()
            self.stdout.write(f"  – asignacion {asignacion.recurso.nombre} → {asignacion.proyecto.codigo}")

        for proyecto in proyectos:
            proyecto.delete()
            self.stdout.write(f"  – proyecto {proyecto.codigo}")

        for recurso in Recurso.objects.filter(email__startswith=PREFIJO_CUENTA):
            recurso.delete()
            self.stdout.write(f"  – recurso {recurso.nombre}")

        for cuenta in cuentas:
            cuenta.is_active = False
            cuenta.save(update_fields=["is_active"])
            self.stdout.write(f"  – cuenta {cuenta.username} desactivada")

        self.stdout.write(
            self.style.SUCCESS(
                "\nFixture retirado. Todo por soft-delete: las filas siguen en la base."
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "FALTA A MANO: desactivar qa.pm y qa.admin en Entra ID. Mientras sigan\n"
                "activas ahi, qa.admin puede aprobar y revocar asignaciones reales."
            )
        )
