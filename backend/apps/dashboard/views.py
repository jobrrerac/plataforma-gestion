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
from apps.core.models import Recurso, Proyecto, Skill
from apps.assignments.models import Asignacion
from apps.calendar_engine.services import CalendarioRango
from decimal import Decimal
from math import ceil
from apps.assignments.services import disponibilidad_recursos, crear_solicitud, analizar_conflictos, capacidad_maxima_dia


class PMOAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Solo PM y Admin pueden acceder. Ingeniero → 403."""
    login_url = "/login/"
    raise_exception = True  # devuelve 403 en vez de redirigir al login si ya está autenticado

    def test_func(self):
        u = self.request.user
        return u.is_superuser or u.groups.filter(name__in=["Admin", "PM"]).exists()


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

        recursos = list(Recurso.objects.filter(activo=True).order_by("nombre"))

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

        puede_ver_datos_personales = (
            request.user.is_superuser
            or request.user.groups.filter(name__in=["Admin", "PM"]).exists()
        )

        result = []
        for recurso in recursos:
            asig_recurso = [a for a in asignaciones if a.recurso_id == recurso.pk]

            detalle_por_dia = []
            cur = fecha_inicio
            while cur <= fecha_fin:
                habil = cal.es_habil(cur, recurso)
                if habil:
                    asig_hoy = [a for a in asig_recurso if a.fecha_inicio <= cur <= a.fecha_fin]
                    horas = sum(float(a.intensidad_diaria) for a in asig_hoy)
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
                asig_hoy_list = [a for a in asig_recurso if a.fecha_inicio <= hoy <= a.fecha_fin]
                horas_hoy = sum(float(a.intensidad_diaria) for a in asig_hoy_list)
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
            if puede_ver_datos_personales:
                entry["email"] = recurso.email
            result.append(entry)

        return Response({
            "periodo": {"inicio": fecha_inicio.isoformat(), "fin": fecha_fin.isoformat()},
            "hoy": hoy.isoformat(),
            "recursos": result,
        })


class SolicitudView(PMOAdminRequiredMixin, View):
    """Buscador de disponibilidad de recursos para crear solicitudes de asignación."""

    def get(self, request):
        fecha_inicio_str = request.GET.get("fecha_inicio", "")
        fecha_fin_str    = request.GET.get("fecha_fin", "")
        horas_str        = request.GET.get("horas_totales", "")
        intensidad_str   = request.GET.get("intensidad_busqueda", "")
        modo_busqueda    = request.GET.get("modo_busqueda", "rango")  # "rango" | "horas"
        skills_sel       = request.GET.getlist("skills")

        resultados = None
        error = None
        fi = ff = None

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
                        resultados = disponibilidad_recursos(fi, ff, skills_sel or None)

            elif fecha_inicio_str and fecha_fin_str:
                ff = date.fromisoformat(fecha_fin_str)
                if ff < fi:
                    error = "La fecha fin debe ser posterior a la fecha de inicio."
                elif (ff - fi).days > 180:
                    error = "El rango máximo de búsqueda es 180 días."
                else:
                    resultados = disponibilidad_recursos(fi, ff, skills_sel or None)

        except (ValueError, TypeError):
            error = "Valores inválidos en el formulario."

        return render(request, "dashboard/solicitud.html", {
            "resultados": resultados,
            "skills_disponibles": Skill.objects.all(),
            "skills_seleccionados": skills_sel,
            "fecha_inicio": fecha_inicio_str,
            "fecha_fin": fecha_fin_str,
            "horas_totales": horas_str,
            "intensidad_busqueda": intensidad_str,
            "modo_busqueda": modo_busqueda,
            "error": error,
            "puede_ver_costos": not request.user.groups.filter(name="Ingeniero").exists(),
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

        try:
            recurso = Recurso.objects.prefetch_related("recurso_skills__skill").get(pk=recurso_id, activo=True)
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
            "puede_ver_costos": not request.user.groups.filter(name="Ingeniero").exists(),
            "modo_crear": modo_crear,
            "horas_crear": horas_crear,
            "intensidad_crear": intensidad_crear,
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

        # Crear la solicitud (con fecha recomputada si había conflictos)
        fecha_fin_final = nueva_fecha_fin if conflict_dates else ff
        horas_final = nuevas_horas if conflict_dates else horas

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
            "fue_recomputada": bool(conflict_dates),
            "conflict_dates_orig": conflict_dates,
        })


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
