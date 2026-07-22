from datetime import date, timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views import View
from apps.accounts.roles import es_admin_o_pm, puede_ver_costos, puede_ver_datos_personales
from apps.core.models import Recurso, Proyecto, Skill, recursos_asignables
from apps.assignments.models import Asignacion
from apps.calendar_engine.services import CalendarioRango
from decimal import Decimal
from math import ceil
import json

from apps.assignments.services import (
    disponibilidad_recursos, crear_solicitud, analizar_conflictos,
    capacidad_maxima_dia, mapa_carga,
    analizar_recurrencia, crear_solicitudes_recurrentes, SEMANAS_MAX_RECURRENCIA,
    segmentos_tarifa, costo_estimado_asignacion,
)


class PMOAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Solo PM y Admin pueden acceder. Ingeniero → 403."""
    login_url = "/login/"
    raise_exception = True  # devuelve 403 en vez de redirigir al login si ya está autenticado

    def test_func(self):
        return es_admin_o_pm(self.request.user)


class OcupacionDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/ocupacion.html"
    login_url = "/login/"


class OcupacionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        hoy = date.today()
        fecha_inicio_str = request.query_params.get("fecha_inicio")
        fecha_fin_str = request.query_params.get("fecha_fin")

        try:
            fecha_inicio = date.fromisoformat(fecha_inicio_str) if fecha_inicio_str else date(hoy.year, hoy.month, 1)
            if fecha_fin_str:
                fecha_fin = date.fromisoformat(fecha_fin_str)
            else:
                siguiente = (fecha_inicio.replace(day=28) + timedelta(days=4))
                fecha_fin = siguiente.replace(day=1) - timedelta(days=1)
        except ValueError:
            return Response({"error": "Formato inválido. Use YYYY-MM-DD."}, status=400)

        if (fecha_fin - fecha_inicio).days > 90:
            return Response({"error": "El rango máximo es 90 días."}, status=400)

        # Solo recursos asignables: los Admin/PM/staff no aparecen en el heatmap
        recursos = list(recursos_asignables().order_by("nombre"))

        # Prefetch de todas las asignaciones aprobadas en el rango (una sola query)
        asignaciones = list(
            Asignacion.objects.filter(
                estado="APROBADA",
                fecha_inicio__lte=fecha_fin,
                fecha_fin__gte=fecha_inicio,
            ).select_related("recurso", "proyecto")
        )

        # Calendario precargado (días no laborables + indisponibilidades) en 2 queries
        cal = CalendarioRango(fecha_inicio, fecha_fin, recursos)

        # Carga diaria neta por recurso (incluye el efecto de cesiones de horas)
        cargas = mapa_carga([r.pk for r in recursos], fecha_inicio, fecha_fin)

        ve_datos_personales = puede_ver_datos_personales(request.user)

        result = []
        for recurso in recursos:
            asig_recurso = [a for a in asignaciones if a.recurso_id == recurso.pk]
            carga_dias = cargas.get(recurso.pk, {})

            detalle_por_dia = []
            cur = fecha_inicio
            while cur <= fecha_fin:
                habil = cal.es_habil(cur, recurso)
                if habil:
                    asig_hoy = [a for a in asig_recurso if a.fecha_inicio <= cur <= a.fecha_fin]
                    horas = carga_dias.get(cur, 0.0)
                    detalle_por_dia.append({
                        "fecha": cur.isoformat(),
                        "horas_asignadas": round(horas, 2),
                        "porcentaje": min(100, round((horas / capacidad_maxima_dia(cur)) * 100, 1)),
                        "proyectos": list({a.proyecto.codigo for a in asig_hoy}),
                    })
                else:
                    detalle_por_dia.append({
                        "fecha": cur.isoformat(),
                        "no_habil": True,
                        "horas_asignadas": 0,
                        "porcentaje": 0,
                        "proyectos": [],
                    })
                cur += timedelta(days=1)

            # Estado del día de hoy
            if fecha_inicio <= hoy <= fecha_fin:
                horas_hoy = carga_dias.get(hoy, 0.0)
            else:
                horas_hoy = 0

            entry = {
                "id": recurso.pk,
                "nombre": recurso.nombre,
                "banda": recurso.get_banda_display(),
                "estado": "BENCH" if horas_hoy == 0 else "OCUPADO",
                "horas_hoy": horas_hoy,
                "porcentaje_hoy": min(100, round((horas_hoy / capacidad_maxima_dia(hoy)) * 100, 1)),
                "asignaciones_activas": len(asig_recurso),
                "detalle_por_dia": detalle_por_dia,
            }
            if ve_datos_personales:
                entry["email"] = recurso.email
            result.append(entry)

        return Response({
            "periodo": {"inicio": fecha_inicio.isoformat(), "fin": fecha_fin.isoformat()},
            "hoy": hoy.isoformat(),
            "recursos": result,
        })


def marcar_elegibilidad(resultados, dias_flexibles, modo_busqueda, horas_requeridas=None):
    """
    Marca cada resultado del buscador con `elegible` y `motivo_no_elegible`.

    Sin "días flexibles" (default) el PM solo puede seleccionar recursos que
    caben en lo pedido tal cual:
      - modo horas: horas libres del recurso ≥ horas solicitadas.
      - modo rango: ningún día del rango completamente lleno (la solicitud no
        necesitaría recomputo para entrar).
    Con "días flexibles" basta con que haya alguna hora libre: el sistema
    ajusta saltando días llenos o extendiendo la fecha fin.
    """
    for r in resultados:
        if dias_flexibles:
            r["elegible"] = r["horas_libres"] > 0
            r["motivo_no_elegible"] = "" if r["elegible"] else "Sin horas libres en el período."
        elif modo_busqueda == "horas" and horas_requeridas:
            r["elegible"] = r["horas_libres"] >= horas_requeridas
            r["motivo_no_elegible"] = "" if r["elegible"] else (
                f"Solo {r['horas_libres']} h libres de las {horas_requeridas} h solicitadas. "
                "Activá 'Días flexibles' para extender la fecha fin."
            )
        else:
            r["elegible"] = r["horas_libres"] > 0 and r["dias_sin_cupo"] == 0
            r["motivo_no_elegible"] = "" if r["elegible"] else (
                f"{r['dias_sin_cupo']} día(s) del rango sin cupo. "
                "Activá 'Días flexibles' para saltarlos recomputando la fecha fin."
                if r["horas_libres"] > 0 else "Sin horas libres en el período."
            )
    return resultados


class SolicitudView(PMOAdminRequiredMixin, View):
    """Buscador de disponibilidad de recursos para crear solicitudes de asignación."""

    def get(self, request):
        fecha_inicio_str = request.GET.get("fecha_inicio", "")
        fecha_fin_str    = request.GET.get("fecha_fin", "")
        horas_str        = request.GET.get("horas_totales", "")
        intensidad_str   = request.GET.get("intensidad_busqueda", "")
        modo_busqueda    = request.GET.get("modo_busqueda", "rango")  # "rango" | "horas"
        skills_sel       = request.GET.getlist("skills")
        nombre_busqueda  = request.GET.get("nombre", "").strip()
        dias_flexibles   = request.GET.get("dias_flexibles") == "on"

        resultados = None
        error = None
        fi = ff = None
        horas_total = None

        try:
            if fecha_inicio_str:
                fi = date.fromisoformat(fecha_inicio_str)

            if modo_busqueda == "horas" and fi and horas_str and intensidad_str:
                from apps.calendar_engine.services import calcular_fecha_fin as _cal_ff
                horas_total = int(horas_str)
                intensidad  = float(intensidad_str.replace(",", "."))
                if horas_total <= 0 or intensidad <= 0:
                    error = "Horas e intensidad deben ser mayores que 0."
                elif intensidad > 8.5:
                    error = "Intensidad máxima: 8.5 h/día."
                else:
                    from math import ceil as _ceil
                    dias_nec = _ceil(horas_total / intensidad)
                    if dias_nec > 180:
                        error = "El rango calculado supera los 180 días hábiles permitidos."
                    else:
                        ff = _cal_ff(fi, dias_nec)
                        fecha_fin_str = ff.isoformat()
                        resultados = disponibilidad_recursos(fi, ff, skills_sel or None, nombre_busqueda or None)

            elif fecha_inicio_str and fecha_fin_str:
                ff = date.fromisoformat(fecha_fin_str)
                if ff < fi:
                    error = "La fecha fin debe ser posterior a la fecha de inicio."
                elif (ff - fi).days > 180:
                    error = "El rango máximo de búsqueda es 180 días."
                else:
                    resultados = disponibilidad_recursos(fi, ff, skills_sel or None, nombre_busqueda or None)

        except (ValueError, TypeError):
            error = "Valores inválidos en el formulario."

        if resultados is not None:
            marcar_elegibilidad(resultados, dias_flexibles, modo_busqueda, horas_total)

        return render(request, "dashboard/solicitud.html", {
            "resultados": resultados,
            "skills_disponibles": Skill.objects.all(),
            "skills_seleccionados": skills_sel,
            "nombre_busqueda": nombre_busqueda,
            "fecha_inicio": fecha_inicio_str,
            "fecha_fin": fecha_fin_str,
            "horas_totales": horas_str,
            "intensidad_busqueda": intensidad_str,
            "modo_busqueda": modo_busqueda,
            "dias_flexibles": dias_flexibles,
            "error": error,
            "puede_ver_costos": puede_ver_costos(request.user),
        })


class SolicitudCrearView(PMOAdminRequiredMixin, View):
    """Formulario simple para crear una solicitud de asignación para un recurso y período ya elegidos."""

    def _get_context(self, request):
        recurso_id = request.GET.get("recurso") or request.POST.get("recurso")
        fecha_inicio_str = request.GET.get("fecha_inicio") or request.POST.get("fecha_inicio")
        fecha_fin_str = request.GET.get("fecha_fin") or request.POST.get("fecha_fin")
        modo_crear = request.GET.get("modo_crear") or request.POST.get("modo_crear", "rango")
        horas_crear = request.GET.get("horas_crear") or request.POST.get("horas_crear", "")
        intensidad_crear = request.GET.get("intensidad_crear") or request.POST.get("intensidad_crear", "")
        dias_flexibles = (request.GET.get("dias_flexibles") or request.POST.get("dias_flexibles")) == "on"

        try:
            recurso = recursos_asignables().prefetch_related("recurso_skills__skill").get(pk=recurso_id)
            fi = date.fromisoformat(fecha_inicio_str)
            ff = date.fromisoformat(fecha_fin_str) if fecha_fin_str else fi
        except Exception:
            return None

        from apps.assignments.services import disponibilidad_recursos, detalle_dias_recurso
        from apps.core.models import TarifaVigente
        disp = disponibilidad_recursos(fi, ff, None)
        info_recurso = next((r for r in disp if r["recurso"].pk == recurso.pk), None)
        detalle_dias = detalle_dias_recurso(recurso, fi, ff)
        tarifa = TarifaVigente.vigente_para(recurso, fi)

        from apps.calendar_engine.services import contar_dias_habiles
        dias_habiles = contar_dias_habiles(fi, ff, recurso)

        # Tramos de tarifa del período (costo mixto por día). En modo "por
        # horas" la fecha fin real puede extenderse: ampliamos el horizonte
        # para que el estimador tenga tarifa de los días adicionales.
        hasta_seg = ff if modo_crear != "horas" else max(ff, fi + timedelta(days=180))
        segmentos = segmentos_tarifa(recurso, fi, hasta_seg)
        hay_tarifa = any(s["valor"] is not None for s in segmentos)
        ve_costos = puede_ver_costos(request.user)
        segmentos_json = json.dumps([
            {
                "dias": s["dias_habiles"],
                "horasMax": s["horas_max"],
                "valor": float(s["valor"]) if s["valor"] is not None else None,
            }
            for s in segmentos
        ]) if ve_costos else "[]"

        return {
            "recurso": recurso,
            "proyectos": Proyecto.objects.filter(estado="ACTIVO").order_by("codigo"),
            "fecha_inicio": fecha_inicio_str,
            "fecha_fin": fecha_fin_str,
            "fi": fi,
            "ff": ff,
            "info": info_recurso,
            "detalle_dias": detalle_dias,
            "dias_habiles": dias_habiles,
            "tarifa": tarifa,
            "segmentos": segmentos,
            "segmentos_json": segmentos_json,
            "hay_tarifa": hay_tarifa,
            "puede_ver_costos": ve_costos,
            "modo_crear": modo_crear,
            "horas_crear": horas_crear,
            "intensidad_crear": intensidad_crear,
            "dias_flexibles": dias_flexibles,
        }

    def get(self, request):
        ctx = self._get_context(request)
        if ctx is None:
            return render(request, "dashboard/solicitud_crear.html", {"error_parametros": True})
        return render(request, "dashboard/solicitud_crear.html", ctx)

    def _validar_form(self, request, ctx):
        """Valida los campos del formulario. Retorna (proyecto, intensidad, jornada_completa, errores)."""
        proyecto_id = request.POST.get("proyecto")
        intensidad_raw = request.POST.get("intensidad_diaria", "").strip()
        jornada_completa = request.POST.get("jornada_completa") == "on"

        errores = []
        proyecto = None
        if not proyecto_id:
            errores.append("Debés seleccionar un proyecto.")
        else:
            try:
                proyecto = Proyecto.objects.get(pk=proyecto_id, estado="ACTIVO")
            except Proyecto.DoesNotExist:
                errores.append("Proyecto inválido.")

        intensidad = None
        if not jornada_completa:
            try:
                intensidad = float(intensidad_raw)
                if intensidad <= 0 or intensidad > 8.5:
                    errores.append("Intensidad diaria debe estar entre 0.5 y 8.5 horas.")
            except ValueError:
                errores.append("Intensidad diaria inválida.")

        return proyecto, intensidad, jornada_completa, errores

    def _post_horas(self, request, ctx, recurso, fi):
        """Crea la solicitud en modo 'Por horas totales', calculando fecha_fin real."""
        from apps.assignments.services import calcular_solicitud_horas, crear_solicitud_por_horas

        horas_raw = request.POST.get("horas_crear", "").strip()
        jornada_completa = request.POST.get("jornada_completa") == "on"
        intensidad_raw = request.POST.get("intensidad_diaria", "").strip()
        proyecto_id = request.POST.get("proyecto")

        errores = []
        horas_target = None
        try:
            horas_target = int(float(horas_raw))
            if horas_target <= 0:
                errores.append("Las horas requeridas deben ser mayores que 0.")
            elif horas_target > 2000:
                errores.append("Máximo 2000 horas por solicitud.")
        except (ValueError, TypeError):
            errores.append("Número de horas inválido.")

        intensidad = None
        if not jornada_completa:
            try:
                intensidad = float(intensidad_raw.replace(",", "."))
                if intensidad <= 0 or intensidad > 8.5:
                    errores.append("Intensidad diaria debe estar entre 0.5 y 8.5 h.")
            except (ValueError, TypeError):
                errores.append("Intensidad diaria inválida.")

        proyecto = None
        if not proyecto_id:
            errores.append("Debés seleccionar un proyecto.")
        else:
            try:
                proyecto = Proyecto.objects.get(pk=proyecto_id, estado="ACTIVO")
            except Proyecto.DoesNotExist:
                errores.append("Proyecto inválido.")

        if errores:
            ctx["errores"] = errores
            ctx["post"] = request.POST
            return render(request, "dashboard/solicitud_crear.html", ctx)

        # Sin días flexibles el relleno no puede saltar días ocupados: si el
        # cálculo detecta días bloqueados, el recurso no está disponible tal cual.
        if not ctx["dias_flexibles"]:
            _, _, _, dias_bloqueados_previos = calcular_solicitud_horas(
                recurso, fi, float(horas_target), intensidad, jornada_completa,
            )
            if dias_bloqueados_previos:
                ctx["errores"] = [
                    f"El recurso tiene {len(dias_bloqueados_previos)} día(s) sin cupo en el período "
                    "necesario para completar las horas. Activá 'Días flexibles' en la búsqueda "
                    "para saltarlos extendiendo la fecha fin."
                ]
                ctx["post"] = request.POST
                return render(request, "dashboard/solicitud_crear.html", ctx)

        asignacion, dias_bloqueados = crear_solicitud_por_horas(
            recurso=recurso,
            proyecto=proyecto,
            fecha_inicio=fi,
            horas_target=horas_target,
            intensidad=intensidad,
            jornada_completa=jornada_completa,
            solicitante=request.user,
        )

        return render(request, "dashboard/solicitud_crear.html", {
            **ctx,
            "asignacion_creada": asignacion,
            "costo_creada": costo_estimado_asignacion(asignacion),
            "dias_bloqueados": dias_bloqueados,
            "fue_recomputada": bool(dias_bloqueados),
            "conflict_dates_orig": dias_bloqueados,
        })

    def post(self, request):
        ctx = self._get_context(request)
        if ctx is None:
            return render(request, "dashboard/solicitud_crear.html", {"error_parametros": True})

        recurso = ctx["recurso"]
        fi = ctx["fi"]
        ff = ctx["ff"]
        modo_crear = request.POST.get("modo_crear", "rango")

        # ── Modo "Por horas totales": calcula fecha_fin respetando ocupación existente ──
        if modo_crear == "horas":
            return self._post_horas(request, ctx, recurso, fi)

        proyecto, intensidad, jornada_completa, errores = self._validar_form(request, ctx)

        if errores:
            ctx["errores"] = errores
            ctx["post"] = request.POST
            return render(request, "dashboard/solicitud_crear.html", ctx)

        confirmado = request.POST.get("confirmado") == "1"

        # Construir asignación temporal para analizar conflictos (sin guardar)
        from apps.calendar_engine.services import contar_dias_habiles
        from apps.assignments.services import calcular_horas_jornada_completa
        dias = contar_dias_habiles(fi, ff, recurso)
        if jornada_completa:
            intensidad_dec = Decimal("8.0")
            horas = calcular_horas_jornada_completa(fi, ff, recurso)
        else:
            intensidad_dec = Decimal(str(intensidad))
            horas = ceil(dias * intensidad)

        asig_temp = Asignacion(
            recurso=recurso,
            proyecto=proyecto,
            modo_asignacion="RANGO",
            fecha_inicio=fi,
            fecha_fin=ff,
            dias_habiles=dias,
            horas_totales=horas,
            intensidad_diaria=intensidad_dec,
            jornada_completa=jornada_completa,
            estado="SOLICITADA",
            solicitada_por=request.user,
        )
        conflict_dates, nueva_fecha_fin, nuevas_horas = analizar_conflictos(asig_temp)

        # Sin días flexibles no se ofrece recomputo: el rango pedido debe entrar tal cual
        if conflict_dates and not ctx["dias_flexibles"]:
            ctx["errores"] = [
                f"El recurso no está disponible en {len(conflict_dates)} día(s) del rango solicitado. "
                "Activá 'Días flexibles' en la búsqueda si aceptás recomputar la fecha fin saltándolos."
            ]
            ctx["post"] = request.POST
            return render(request, "dashboard/solicitud_crear.html", ctx)

        if conflict_dates and not confirmado:
            # Mostrar alerta de conflictos y pedir confirmación
            ctx.update({
                "conflict_dates": conflict_dates,
                "nueva_fecha_fin": nueva_fecha_fin,
                "nuevas_horas": nuevas_horas,
                "post": request.POST,
                "proyecto_sel": proyecto,
            })
            return render(request, "dashboard/solicitud_crear.html", ctx)

        # Crear la solicitud (con fecha recomputada si había conflictos).
        # Nota: crear_solicitud recalcula las horas a partir del rango final;
        # nuevas_horas solo se usa para mostrar la alerta de confirmación.
        fecha_fin_final = nueva_fecha_fin if conflict_dates else ff

        asignacion = crear_solicitud(
            recurso=recurso,
            proyecto=proyecto,
            fecha_inicio=fi,
            fecha_fin=fecha_fin_final,
            intensidad_diaria=intensidad,
            jornada_completa=jornada_completa,
            solicitante=request.user,
        )

        return render(request, "dashboard/solicitud_crear.html", {
            **ctx,
            "asignacion_creada": asignacion,
            "costo_creada": costo_estimado_asignacion(asignacion),
            "fue_recomputada": bool(conflict_dates),
            "conflict_dates_orig": conflict_dates,
        })


class SolicitudRecurrenteView(PMOAdminRequiredMixin, View):
    """
    Solicitud con patrón semanal, como repetir una sesión en Teams:
    "próximos 4 lunes 4 h" o "2 semanas: lunes 2 h, miércoles 4 h, viernes 2 h".
    Genera una asignación de un día por ocurrencia, agrupadas en una serie.
    """

    DIAS = [(0, "Lunes"), (1, "Martes"), (2, "Miércoles"), (3, "Jueves"), (4, "Viernes")]
    template = "dashboard/solicitud_recurrente.html"

    def _ctx_base(self, request, datos):
        """El recurso es opcional al entrar: la pantalla ofrece un selector."""
        ctx = {
            "recurso": None,
            "recursos": recursos_asignables().order_by("nombre"),
            "proyectos": Proyecto.objects.filter(estado="ACTIVO").order_by("codigo"),
            "semanas_max": SEMANAS_MAX_RECURRENCIA,
        }
        recurso_id = datos.get("recurso")
        if recurso_id:
            try:
                ctx["recurso"] = recursos_asignables().get(pk=recurso_id)
            except (Recurso.DoesNotExist, ValueError, TypeError):
                pass
        return ctx

    def _dias_form(self, datos):
        """Los 5 días hábiles con el valor de horas que trajo el formulario."""
        return [
            {"num": num, "nombre": nombre, "valor": (datos.get(f"h_{num}") or "").strip()}
            for num, nombre in self.DIAS
        ]

    def _parse_patron(self, datos):
        """Valida y convierte el formulario. Retorna (fi, semanas, horas_por_dia, errores)."""
        errores = []
        fi = None
        try:
            fi = date.fromisoformat(datos.get("fecha_inicio", ""))
        except (ValueError, TypeError):
            errores.append("Fecha de inicio inválida.")

        semanas = 0
        try:
            semanas = int(datos.get("semanas", ""))
            if not (1 <= semanas <= SEMANAS_MAX_RECURRENCIA):
                errores.append(f"Semanas debe estar entre 1 y {SEMANAS_MAX_RECURRENCIA}.")
        except (ValueError, TypeError):
            errores.append("Número de semanas inválido.")

        horas_por_dia = {}
        for num, nombre in self.DIAS:
            raw = (datos.get(f"h_{num}") or "").strip()
            if not raw:
                continue
            try:
                horas = float(raw.replace(",", "."))
            except ValueError:
                errores.append(f"{nombre}: horas inválidas.")
                continue
            if horas <= 0:
                continue
            if horas > 8.5:
                errores.append(f"{nombre}: máximo 8.5 h/día.")
                continue
            horas_por_dia[num] = horas
        if not horas_por_dia and not errores:
            errores.append("Indicá las horas de al menos un día de la semana.")

        return fi, semanas, horas_por_dia, errores

    def get(self, request):
        ctx = self._ctx_base(request, request.GET)
        ctx.update({
            "fecha_inicio": request.GET.get("fecha_inicio", ""),
            "semanas": request.GET.get("semanas", "1"),
            "dias_form": self._dias_form(request.GET),
            "dias_flexibles": request.GET.get("dias_flexibles") == "on",
        })
        return render(request, self.template, ctx)

    def post(self, request):
        ctx = self._ctx_base(request, request.POST)
        datos = request.POST
        ctx.update({
            "fecha_inicio": datos.get("fecha_inicio", ""),
            "semanas": datos.get("semanas", ""),
            "dias_form": self._dias_form(datos),
            "proyecto_sel": datos.get("proyecto", ""),
            "dias_flexibles": datos.get("dias_flexibles") == "on",
        })

        fi, semanas, horas_por_dia, errores = self._parse_patron(datos)

        if ctx["recurso"] is None:
            errores.insert(0, "Seleccioná un recurso.")

        proyecto = None
        if not datos.get("proyecto"):
            errores.append("Debés seleccionar un proyecto.")
        else:
            try:
                proyecto = Proyecto.objects.get(pk=datos["proyecto"], estado="ACTIVO")
            except (Proyecto.DoesNotExist, ValueError):
                errores.append("Proyecto inválido.")

        if errores:
            ctx["errores"] = errores
            return render(request, self.template, ctx)

        if datos.get("accion") == "confirmar":
            # Sin días flexibles no se aceptan sesiones perdidas por falta de
            # cupo (los feriados se omiten igual: no dependen de la ocupación).
            if not ctx["dias_flexibles"]:
                plan = analizar_recurrencia(ctx["recurso"], fi, semanas, horas_por_dia)
                sin_cupo = [p for p in plan if p["estado"] == "SIN_CUPO"]
                if sin_cupo:
                    fechas = ", ".join(p["fecha"].strftime("%d/%m") for p in sin_cupo)
                    ctx["errores"] = [
                        f"El recurso no tiene cupo en {len(sin_cupo)} sesión(es) del patrón ({fechas}). "
                        "Activá 'Días flexibles' si aceptás crear la serie omitiendo esos días."
                    ]
                    ctx.update({
                        "plan": plan,
                        "plan_ok": sum(1 for p in plan if p["estado"] == "OK"),
                        "plan_horas": sum(p["horas"] for p in plan if p["estado"] == "OK"),
                    })
                    return render(request, self.template, ctx)
            try:
                serie, creadas, omitidas = crear_solicitudes_recurrentes(
                    ctx["recurso"], proyecto, fi, semanas, horas_por_dia, request.user,
                )
            except ValueError as e:
                ctx["errores"] = [str(e)]
                return render(request, self.template, ctx)
            ctx.update({
                "serie": serie,
                "creadas": creadas,
                "omitidas": omitidas,
                "total_horas": sum(float(a.intensidad_diaria) for a in creadas),
            })
            return render(request, self.template, ctx)

        # Previsualización: mostrar el plan día a día antes de confirmar
        plan = analizar_recurrencia(ctx["recurso"], fi, semanas, horas_por_dia)
        ctx.update({
            "plan": plan,
            "plan_ok": sum(1 for p in plan if p["estado"] == "OK"),
            "plan_horas": sum(p["horas"] for p in plan if p["estado"] == "OK"),
        })
        return render(request, self.template, ctx)


@method_decorator(login_required(login_url="/login/"), name="dispatch")
class RecursoDetalleView(View):
    """Detalle de un recurso: asignaciones en curso y próximas."""

    def get(self, request, pk):
        recurso = get_object_or_404(
            Recurso.objects.prefetch_related("recurso_skills__skill"),
            pk=pk, activo=True,
        )
        hoy = date.today()
        asignaciones = list(
            Asignacion.objects
            .filter(recurso=recurso, estado__in=["APROBADA", "SOLICITADA"], fecha_fin__gte=hoy)
            .select_related("proyecto")
            .order_by("fecha_inicio")
        )
        en_curso = [a for a in asignaciones if a.fecha_inicio <= hoy]
        proximas = [a for a in asignaciones if a.fecha_inicio > hoy]

        return render(request, "dashboard/recurso_detalle.html", {
            "recurso": recurso,
            "en_curso": en_curso,
            "proximas": proximas,
            "hoy": hoy,
        })


class LiberacionSolicitarView(PMOAdminRequiredMixin, View):
    """
    Página del PM para solicitar la liberación temporal de un recurso en una
    ventana. La solicitud queda PENDIENTE (no libera cupo) hasta que un Admin la
    aprueba. El PM solo ve las asignaciones aprobadas de SUS proyectos; el Admin
    las ve todas.
    """
    template = "dashboard/liberacion_solicitar.html"

    def _asignaciones_visibles(self, request):
        from apps.accounts.roles import es_admin
        qs = (
            Asignacion.objects.filter(estado="APROBADA")
            .select_related("recurso", "proyecto").order_by("recurso__nombre", "fecha_inicio")
        )
        if not es_admin(request.user):
            qs = qs.filter(proyecto__pm=request.user)
        return qs

    def _base_ctx(self, request, asignacion=None):
        from apps.assignments.models import LiberacionRecurso
        ctx = {
            "asignaciones": self._asignaciones_visibles(request),
            "asignacion_sel": asignacion,
            "hoy": date.today(),
        }
        if asignacion is not None:
            ctx["liberaciones"] = LiberacionRecurso.objects.filter(
                asignacion=asignacion
            ).select_related("solicitada_por", "revisada_por").order_by("-creado_en")
        return ctx

    def _get_asignacion(self, request, pk):
        if not pk:
            return None
        try:
            return self._asignaciones_visibles(request).get(pk=pk)
        except Asignacion.DoesNotExist:
            return None

    def get(self, request):
        asignacion = self._get_asignacion(request, request.GET.get("asignacion"))
        return render(request, self.template, self._base_ctx(request, asignacion))

    def post(self, request):
        from apps.assignments.services import solicitar_liberacion

        asignacion = self._get_asignacion(request, request.POST.get("asignacion"))
        ctx = self._base_ctx(request, asignacion)
        if asignacion is None:
            ctx["error"] = "Asignación inválida o fuera de tu alcance."
            return render(request, self.template, ctx)

        try:
            fecha_inicio = date.fromisoformat(request.POST.get("fecha_inicio", ""))
            fecha_fin = date.fromisoformat(request.POST.get("fecha_fin", ""))
            politica = request.POST.get("politica", "")
            motivo = (request.POST.get("motivo") or "").strip()
        except (ValueError, TypeError):
            ctx["error"] = "Formulario incompleto o con fechas inválidas."
            ctx["post"] = request.POST
            return render(request, self.template, ctx)

        try:
            liberacion = solicitar_liberacion(asignacion, fecha_inicio, fecha_fin, politica, motivo, request.user)
        except ValueError as e:
            ctx["error"] = str(e)
            ctx["post"] = request.POST
            return render(request, self.template, ctx)

        ctx = self._base_ctx(request, asignacion)
        ctx["exito"] = liberacion
        return render(request, self.template, ctx)


class CesionSolicitarView(PMOAdminRequiredMixin, View):
    """
    Página del PM para ceder horas de un día de una asignación aprobada a otro
    proyecto. Al crear la cesión se genera una asignación destino SOLICITADA que
    un Admin debe aprobar (ese es el gate); hasta entonces las horas quedan
    RESERVADAS. El PM solo ve las asignaciones aprobadas de SUS proyectos.
    """
    template = "dashboard/cesion_solicitar.html"

    def _asignaciones_visibles(self, request):
        from apps.accounts.roles import es_admin
        qs = (
            Asignacion.objects.filter(estado="APROBADA")
            .select_related("recurso", "proyecto").order_by("recurso__nombre", "fecha_inicio")
        )
        if not es_admin(request.user):
            qs = qs.filter(proyecto__pm=request.user)
        return qs

    def _base_ctx(self, request, asignacion=None):
        from apps.assignments.models import CesionHoras
        ctx = {
            "asignaciones": self._asignaciones_visibles(request),
            "asignacion_sel": asignacion,
            "hoy": date.today(),
        }
        if asignacion is not None:
            ctx["proyectos"] = (
                Proyecto.objects.filter(estado="ACTIVO")
                .exclude(pk=asignacion.proyecto_id).order_by("codigo")
            )
            ctx["cesiones"] = (
                CesionHoras.objects.filter(asignacion_origen=asignacion)
                .select_related("asignacion_destino__proyecto", "creado_por")
                .order_by("-creado_en")
            )
        return ctx

    def _get_asignacion(self, request, pk):
        if not pk:
            return None
        try:
            return self._asignaciones_visibles(request).get(pk=pk)
        except Asignacion.DoesNotExist:
            return None

    def get(self, request):
        asignacion = self._get_asignacion(request, request.GET.get("asignacion"))
        return render(request, self.template, self._base_ctx(request, asignacion))

    def post(self, request):
        from apps.assignments.services import ceder_horas

        asignacion = self._get_asignacion(request, request.POST.get("asignacion"))
        ctx = self._base_ctx(request, asignacion)
        if asignacion is None:
            ctx["error"] = "Asignación inválida o fuera de tu alcance."
            return render(request, self.template, ctx)

        try:
            fecha = date.fromisoformat(request.POST.get("fecha", ""))
            horas = float((request.POST.get("horas") or "").replace(",", "."))
            proyecto = Proyecto.objects.get(pk=request.POST.get("proyecto"), estado="ACTIVO")
            politica = request.POST.get("politica", "")
        except (ValueError, TypeError, Proyecto.DoesNotExist):
            ctx["error"] = "Formulario incompleto o con valores inválidos."
            ctx["post"] = request.POST
            return render(request, self.template, ctx)

        try:
            cesion = ceder_horas(asignacion, proyecto, fecha, horas, politica, request.user)
        except ValueError as e:
            ctx["error"] = str(e)
            ctx["post"] = request.POST
            return render(request, self.template, ctx)

        ctx = self._base_ctx(request, asignacion)
        ctx["exito"] = cesion
        return render(request, self.template, ctx)
