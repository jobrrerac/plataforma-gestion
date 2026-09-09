"""El dashboard de un proyecto.

El de ocupación responde «quién está libre». Este responde la otra pregunta, la
de la reunión de seguimiento: **cómo va este proyecto**. Junta el plan, lo
declarado, lo que lo frena y —solo para el Admin— lo que dice de él la gente que
trabaja dentro.

Lo que se blinda aquí: que no filtre a quien no debe verlo, y que la señal más
delicada —lo que el equipo dijo de las condiciones del proyecto— no se le escape
al jefe de ese proyecto, que también entra a esta pantalla.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.legalizacion.models import DiaLegalizado, RegistroHoras, TipoActividad
from apps.seguimiento import services as seg
from apps.seguimiento.models import Feedback


class BaseProyecto(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm_dp", "adp@test.com", "clave-larga-1")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.pm = User.objects.create_user("pm_dp", "pdp@test.com", "clave-larga-1")
        self.pm.groups.add(Group.objects.get_or_create(name="PM")[0])
        self.ing = User.objects.create_user("ing_dp", "idp@test.com", "clave-larga-1")
        self.ing.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])

        self.hoy = timezone.localdate()
        self.recurso = Recurso.objects.create(
            nombre="Trabajadora Diaz", email="idp@test.com", banda="JR", usuario=self.ing,
        )
        self.proyecto = Proyecto.objects.create(
            codigo="DP-01", nombre="Proyecto medido", cliente="ACME", pm=self.pm,
            fecha_inicio=self.hoy - timedelta(days=30),
            fecha_fin=self.hoy + timedelta(days=30),
        )
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=self.hoy - timedelta(days=20),
            fecha_fin=self.hoy + timedelta(days=20),
            horas_totales=100, intensidad_diaria=8.5,
            estado="APROBADA", solicitada_por=self.pm,
        )
        self.tipo = TipoActividad.objects.create(nombre="Proyecto DP", requiere_proyecto=True)

    def _horas(self, horas="8.5", dias_atras=1):
        dia, _ = DiaLegalizado.objects.get_or_create(
            recurso=self.recurso, fecha=self.hoy - timedelta(days=dias_atras),
            defaults={"total_horas": Decimal(horas), "jornada_esperada": Decimal("8.5")},
        )
        return RegistroHoras.objects.create(
            dia=dia, tipo_actividad=self.tipo, proyecto=self.proyecto,
            horas=Decimal(horas), detalle="lo que hizo ese dia",
            estado=RegistroHoras.APROBADO, aprobado_por=self.pm,
            aprobado_en=timezone.now(),
        )

    def _ver(self, usuario, **params):
        self.client.force_login(usuario)
        datos = {"proyecto": self.proyecto.pk}
        datos.update(params)
        resp = self.client.get(reverse("dashboard-proyecto"), datos)
        self.assertEqual(resp.status_code, 200)
        return resp


class QuienPuedeEntrarTests(BaseProyecto):

    def test_el_admin_si(self):
        self._ver(self.admin)

    def test_el_pm_si(self):
        self._ver(self.pm)

    def test_el_ingeniero_no(self):
        """Resume el trabajo de otras personas: no es una pantalla para quien
        solo se ve a si mismo."""
        self.client.force_login(self.ing)
        resp = self.client.get(reverse("dashboard-proyecto"))
        self.assertEqual(resp.status_code, 403)

    def test_sin_sesion_tampoco(self):
        resp = self.client.get(reverse("dashboard-proyecto"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_sin_elegir_proyecto_no_revienta(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("dashboard-proyecto"))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["proyecto"])


class LoQueResumeTests(BaseProyecto):

    def test_cuenta_las_horas_aprobadas(self):
        self._horas("8.5", 1)
        self._horas("4", 2)
        ctx = self._ver(self.admin).context
        self.assertEqual(ctx["horas"], Decimal("12.5"))
        self.assertEqual(ctx["actividades"], 2)

    def test_solo_cuenta_lo_aprobado(self):
        """Lo pendiente todavia puede cambiar. Sumarlo daria un avance que no
        ha firmado nadie."""
        r = self._horas("8.5", 1)
        RegistroHoras.objects.filter(pk=r.pk).update(estado=RegistroHoras.PENDIENTE)
        self.assertEqual(self._ver(self.admin).context["horas"], Decimal("0"))

    def test_agrupa_por_persona(self):
        self._horas("8.5", 1)
        ctx = self._ver(self.admin).context
        fila = ctx["por_recurso"][0]
        self.assertEqual(fila["recurso"], self.recurso)
        self.assertEqual(fila["horas"], Decimal("8.5"))
        self.assertEqual(fila["ultimo"], self.hoy - timedelta(days=1))

    def test_sale_quien_esta_asignado_aunque_no_haya_declarado_nada(self):
        """Es justo el caso que interesa ver: alguien asignado que no ha tocado
        el proyecto. Si solo se listara a quien declaro horas, desaparece."""
        ctx = self._ver(self.admin).context
        self.assertEqual(len(ctx["por_recurso"]), 1)
        self.assertIsNone(ctx["por_recurso"][0]["ultimo"])

    def test_cuenta_los_bloqueantes_del_proyecto(self):
        seg.abrir_bloqueante(
            self.recurso, self.ing, "el esquema",
            rol_que_resuelve="PM", proyecto=self.proyecto,
        )
        ctx = self._ver(self.admin).context
        self.assertEqual(len(ctx["bloqueantes_abiertos"]), 1)
        self.assertEqual(ctx["por_recurso"][0]["bloqueantes"], 1)

    def test_la_media_de_desbloqueo_solo_mira_los_cerrados(self):
        """Los abiertos siguen contando: meterlos bajaria la media justo cuando
        peor va la cosa."""
        b = seg.abrir_bloqueante(
            self.recurso, self.ing, "uno", rol_que_resuelve="PM", proyecto=self.proyecto,
        )
        seg.resolver_bloqueante(b, self.ing)
        seg.abrir_bloqueante(
            self.recurso, self.ing, "otro abierto",
            rol_que_resuelve="PM", proyecto=self.proyecto,
        )
        ctx = self._ver(self.admin).context
        self.assertIsNotNone(ctx["media_desbloqueo"])
        self.assertEqual(len(ctx["bloqueantes_resueltos"]), 1)

    def test_avisa_si_las_asignaciones_pasan_del_fin_del_proyecto(self):
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=self.hoy, fecha_fin=self.proyecto.fecha_fin + timedelta(days=15),
            horas_totales=40, intensidad_diaria=4,
            estado="APROBADA", solicitada_por=self.pm,
        )
        ctx = self._ver(self.admin).context
        self.assertGreater(ctx["fin_asignaciones"], self.proyecto.fecha_fin)


class LaSenalDelicadaTests(BaseProyecto):
    """Lo que el equipo dijo de las condiciones del proyecto.

    Se pidió con la promesa de que solo lo leyera el manager en Colombia. Esta
    pantalla la abre también el jefe de ese proyecto, así que aquí es donde esa
    promesa se rompería con más facilidad.
    """

    def setUp(self):
        super().setUp()
        seg.registrar_feedback(
            recurso=self.recurso, autor=self.ing,
            direccion=Feedback.RECURSO_A_PROYECTO,
            proyecto=self.proyecto, claridad_objetivo=2,
            comentario="el alcance llegaba a medias",
        )

    def test_el_admin_ve_la_claridad_media(self):
        ctx = self._ver(self.admin).context
        self.assertEqual(ctx["claridad"]["media"], 2)
        self.assertEqual(ctx["claridad"]["cuantos"], 1)

    def test_el_pm_no(self):
        self.assertIsNone(self._ver(self.pm).context["claridad"])

    def test_y_no_se_le_cuela_en_el_html(self):
        """Ni el numero ni la etiqueta: si la tarjeta se pintara vacia, el PM
        sabria que existe y quien la escribio."""
        html = self._ver(self.pm).content.decode()
        self.assertNotIn("Claridad del objetivo", html)
        self.assertNotIn("el alcance llegaba a medias", html)


class ElAlcanceEsPorProyectoTests(BaseProyecto):
    """Quien dirige un proyecto ve ese proyecto, y solo ese.

    Es la misma regla con la que ya se firman las horas: la designacion ES la
    autorizacion, y alcanza justo hasta donde alcanza el proyecto designado.

    Importa porque un aprobador delegado suele ser un Ingeniero, y un Ingeniero
    no ve a nadie mas que a si mismo. Abrirle esta pantalla entera lo llevaria de
    «ve su proyecto» a «ve el equipo completo de la empresa» por una delegacion
    creada para otra cosa.
    """

    def setUp(self):
        super().setUp()
        self.otro_pm = User.objects.create_user("pm2_dp", "p2dp@test.com", "clave-larga-1")
        self.otro_pm.groups.add(Group.objects.get(name="PM"))
        self.ajeno = Proyecto.objects.create(
            codigo="DP-AJENO", nombre="De otro", cliente="OTRA", pm=self.otro_pm,
            fecha_inicio=self.hoy - timedelta(days=10),
            fecha_fin=self.hoy + timedelta(days=10),
        )
        self.delegada = User.objects.create_user("del_dp", "ddp@test.com", "clave-larga-1")
        self.delegada.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])
        self.proyecto.aprobador_delegado = self.delegada
        self.proyecto.save(update_fields=["aprobador_delegado"])

    def _codigos(self, usuario):
        self.client.force_login(usuario)
        resp = self.client.get(reverse("dashboard-proyecto"))
        self.assertEqual(resp.status_code, 200)
        return {p.codigo for p in resp.context["proyectos"]}

    def test_el_pm_solo_ve_los_suyos(self):
        codigos = self._codigos(self.pm)
        self.assertIn("DP-01", codigos)
        self.assertNotIn("DP-AJENO", codigos)

    def test_el_delegado_entra_aunque_sea_ingeniero(self):
        """La delegacion es la autorizacion: no hace falta que tenga un rol."""
        self.assertEqual(self._codigos(self.delegada), {"DP-01"})

    def test_el_admin_los_ve_todos(self):
        codigos = self._codigos(self.admin)
        self.assertIn("DP-01", codigos)
        self.assertIn("DP-AJENO", codigos)

    def test_un_ingeniero_sin_delegacion_sigue_fuera(self):
        """La delegacion no puede convertirse en una puerta para cualquiera."""
        self.client.force_login(self.ing)
        self.assertEqual(
            self.client.get(reverse("dashboard-proyecto")).status_code, 403,
        )

    def test_no_se_cuela_un_proyecto_ajeno_por_la_url(self):
        """Si el filtro se aplicara sobre todos los proyectos, un PM podria leer
        el equipo y las horas de cualquier otro probando ids."""
        self.client.force_login(self.pm)
        resp = self.client.get(
            reverse("dashboard-proyecto"), {"proyecto": self.ajeno.pk},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(
            resp.context["proyecto"],
            "un PM no puede abrir el resumen de un proyecto que no dirige",
        )

    def test_el_delegado_tampoco(self):
        self.client.force_login(self.delegada)
        resp = self.client.get(
            reverse("dashboard-proyecto"), {"proyecto": self.ajeno.pk},
        )
        self.assertIsNone(resp.context["proyecto"])

    def test_al_delegado_le_sale_el_enlace(self):
        """Si no le sale, tiene que saberse la URL de memoria."""
        self.client.force_login(self.delegada)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("/dashboard/proyecto/", html)

        hub = self.client.get(reverse("gestionar"))
        self.assertIn("/dashboard/proyecto/", {i["url"] for i in hub.context["items"]})

    def test_al_ingeniero_sin_delegacion_no(self):
        self.client.force_login(self.ing)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertNotIn("/dashboard/proyecto/", html)

    def test_el_delegado_no_ve_la_senal_reservada(self):
        """Sigue siendo del Admin: es el feedback que se pidio con la promesa de
        que no lo leyera el proyecto, y un delegado es parte del proyecto."""
        seg.registrar_feedback(
            recurso=self.recurso, autor=self.ing,
            direccion=Feedback.RECURSO_A_PROYECTO,
            proyecto=self.proyecto, claridad_objetivo=2,
        )
        self.client.force_login(self.delegada)
        resp = self.client.get(
            reverse("dashboard-proyecto"), {"proyecto": self.proyecto.pk},
        )
        self.assertIsNone(resp.context["claridad"])
        self.assertNotIn("Claridad del objetivo", resp.content.decode())


class ElDetalleDeLoAprobadoTests(BaseProyecto):
    """La tabla existe para que el equipo vea QUE se aprobo, no para sacar
    cifras: esas estan en la cabecera y no cambian al filtrar."""

    def setUp(self):
        super().setUp()
        for n in range(1, 31):
            self._horas("1", n)

    def test_pagina_de_25(self):
        ctx = self._ver(self.admin).context
        self.assertEqual(len(ctx["pagina"].object_list), 25)
        self.assertEqual(ctx["pagina"].paginator.num_pages, 2)
        self.assertEqual(ctx["total_detalle"], 30)

    def test_la_segunda_pagina_trae_el_resto(self):
        ctx = self._ver(self.admin, pagina=2).context
        self.assertEqual(len(ctx["pagina"].object_list), 5)

    def test_filtra_por_fecha(self):
        ctx = self._ver(
            self.admin,
            desde=(self.hoy - timedelta(days=3)).isoformat(),
            hasta=(self.hoy - timedelta(days=1)).isoformat(),
        ).context
        self.assertEqual(ctx["total_detalle"], 3)

    def test_las_cifras_de_cabecera_no_se_mueven(self):
        """Si el resumen del proyecto cambiara al filtrar una semana, dejaria de
        ser el resumen del proyecto."""
        completo = self._ver(self.admin).context["horas"]
        filtrado = self._ver(
            self.admin, desde=self.hoy.isoformat(), hasta=self.hoy.isoformat(),
        ).context
        self.assertEqual(filtrado["horas"], completo)
        self.assertEqual(filtrado["total_detalle"], 0)

    def test_las_fechas_al_reves_se_enderezan(self):
        """Es un error de dedo, no una peticion de ningun resultado."""
        ctx = self._ver(
            self.admin,
            desde=(self.hoy - timedelta(days=1)).isoformat(),
            hasta=(self.hoy - timedelta(days=3)).isoformat(),
        ).context
        self.assertEqual(ctx["total_detalle"], 3)

    def test_una_fecha_ilegible_no_revienta(self):
        """Quien teclea mal una fecha en la barra de direcciones espera ver la
        pantalla, no un 500."""
        ctx = self._ver(self.admin, desde="ayer").context
        self.assertEqual(ctx["total_detalle"], 30)

    def test_dice_quien_aprobo(self):
        html = self._ver(self.admin).content.decode()
        self.assertIn("Aprob", html)
        self.assertIn(self.pm.username, html)


class LaDescargaTests(BaseProyecto):

    def setUp(self):
        super().setUp()
        self._horas("2.5", 1)
        self._horas("3", 2)

    def _tsv(self, usuario, **params):
        self.client.force_login(usuario)
        datos = {"proyecto": self.proyecto.pk, "formato": "tsv"}
        datos.update(params)
        resp = self.client.get(reverse("dashboard-proyecto"), datos)
        self.assertEqual(resp.status_code, 200)
        return resp

    def _filas(self, resp):
        texto = resp.content.decode("utf-8-sig")
        return [linea for linea in texto.split("\n") if linea.strip()]

    def test_sale_como_fichero(self):
        resp = self._tsv(self.admin)
        self.assertIn("tab-separated", resp["Content-Type"])
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertIn(".tsv", resp["Content-Disposition"])

    def test_trae_cabecera_y_filas(self):
        filas = self._filas(self._tsv(self.admin))
        self.assertEqual(filas[0].split("\t")[0], "Dia")
        self.assertIn("Aprobo", filas[0])
        self.assertEqual(len(filas), 3)

    def test_respeta_el_filtro_de_fechas(self):
        filas = self._filas(self._tsv(
            self.admin, desde=(self.hoy - timedelta(days=1)).isoformat(),
        ))
        self.assertEqual(len(filas), 2)

    def test_baja_todo_lo_filtrado_y_no_solo_una_pagina(self):
        """Darle 25 filas a quien pidio el periodo entero porque iba por la
        primera pagina es lo que hace desconfiar del boton."""
        for n in range(3, 40):
            self._horas("1", n)
        self.assertEqual(len(self._filas(self._tsv(self.admin))) - 1, 39)

    def test_el_detalle_con_tabuladores_no_parte_la_fila(self):
        """El detalle es texto libre: un tabulador dentro correria las columnas
        de esa fila y todo lo que venga detras."""
        r = self._horas("1", 5)
        RegistroHoras.objects.filter(pk=r.pk).update(
            detalle="hice\testo\ty tambien\nlo otro",
        )
        for fila in self._filas(self._tsv(self.admin))[1:]:
            self.assertEqual(
                len(fila.split("\t")), 6, f"fila con columnas de mas: {fila!r}",
            )

    def test_no_lleva_ninguna_columna_de_dinero(self):
        """La abre un aprobador delegado, que suele ser Ingeniero, y el
        Ingeniero no ve costos. Es regla no negociable del proyecto."""
        texto = self._tsv(self.admin).content.decode("utf-8-sig").lower()
        for palabra in ("tarifa", "costo", "precio", "importe", "monto"):
            self.assertNotIn(palabra, texto)

    def test_un_ingeniero_no_puede_descargar(self):
        self.client.force_login(self.ing)
        resp = self.client.get(reverse("dashboard-proyecto"), {
            "proyecto": self.proyecto.pk, "formato": "tsv",
        })
        self.assertEqual(resp.status_code, 403)
