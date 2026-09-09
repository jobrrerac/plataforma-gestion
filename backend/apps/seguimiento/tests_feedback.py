"""Observaciones en las dos direcciones, y quién puede leer cada una.

Lo que se blinda aquí son dos cosas que se contradicen solo en apariencia:

- Lo que el proyecto escribe **sobre** una persona, esa persona lo lee. Un
  registro que sirve para evaluar a alguien y que esa persona no puede ver no se
  puede rebatir, y por tanto no se puede considerar justo.
- Lo que una persona escribe **sobre** el proyecto no lo lee el jefe de proyecto.
  Quien depende de otro para que le asignen tareas no va a escribir «llevo tres
  días sin respuesta» sabiendo que esa misma persona lo va a leer con su nombre
  encima.

La asimetría es deliberada y es lo que hace que la segunda dirección produzca
información real en vez de cortesía.
"""

from datetime import date

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.seguimiento import services as svc
from apps.seguimiento.models import Feedback


class BaseFeedback(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm_fb", "af@test.com", "clave-larga-1")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.pm = User.objects.create_user("pm_fb", "pf@test.com", "clave-larga-1")
        self.pm.groups.add(Group.objects.get_or_create(name="PM")[0])
        self.ing = User.objects.create_user("ing_fb", "if@test.com", "clave-larga-1")
        self.ing.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])

        self.recurso = Recurso.objects.create(
            nombre="Observada Gomez", email="if@test.com", banda="JR", usuario=self.ing,
        )
        self.proyecto = Proyecto.objects.create(
            codigo="FB-01", nombre="Proyecto observado", pm=self.pm,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
        )
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
            horas_totales=100, intensidad_diaria=8.5,
            estado="APROBADA", solicitada_por=self.pm,
        )

    def _sobre_la_persona(self, autor=None, **extra):
        datos = dict(
            proyecto=self.proyecto,
            situacion="revision del endpoint del martes",
            conducta="entrego sin probar los casos de borde",
            impacto="hubo que devolverlo y se perdio un dia",
            tipo=Feedback.A_MEJORAR,
        )
        datos.update(extra)
        return svc.registrar_feedback(
            recurso=self.recurso, autor=autor or self.pm,
            direccion=Feedback.PROYECTO_A_RECURSO, **datos
        )

    def _sobre_el_proyecto(self, autor=None, **extra):
        datos = dict(
            proyecto=self.proyecto,
            claridad_objetivo=2,
            tuve_que_intuir=True,
            comentario="la comunicacion diaria bien; el alcance escrito, mal",
        )
        datos.update(extra)
        return svc.registrar_feedback(
            recurso=self.recurso, autor=autor or self.ing,
            direccion=Feedback.RECURSO_A_PROYECTO, **datos
        )


class ObservacionSobreLaPersonaTests(BaseFeedback):

    def test_el_pm_del_proyecto_puede(self):
        f = self._sobre_la_persona()
        self.assertEqual(f.autor, self.pm)
        self.assertEqual(f.direccion, Feedback.PROYECTO_A_RECURSO)

    def test_sin_conducta_observada_no_se_guarda(self):
        """Sin hechos esto es una opinion. Una observacion con fecha se puede
        contrastar y rebatir; un adjetivo no."""
        with self.assertRaises(ValidationError):
            self._sobre_la_persona(conducta="   ")

    def test_sin_decir_si_es_fortaleza_o_mejora_tampoco(self):
        with self.assertRaises(ValidationError):
            self._sobre_la_persona(tipo="")

    def test_la_dimension_es_opcional(self):
        """La observacion es lo que cuenta; la dimension solo agrupa. Exigirla
        convierte el formulario en un tramite."""
        f = self._sobre_la_persona(dimension="")
        self.assertEqual(f.dimension, "")

    def test_un_pm_ajeno_no_observa_a_quien_no_dirige(self):
        otro_pm = User.objects.create_user("pm2_fb", "p2@test.com", "clave-larga-1")
        otro_pm.groups.add(Group.objects.get(name="PM"))
        with self.assertRaises(PermissionDenied):
            self._sobre_la_persona(autor=otro_pm)

    def test_un_ingeniero_no_observa_a_nadie(self):
        with self.assertRaises(PermissionDenied):
            self._sobre_la_persona(autor=self.ing)

    def test_el_admin_si(self):
        f = self._sobre_la_persona(autor=self.admin)
        self.assertEqual(f.autor, self.admin)


class OpinionSobreElProyectoTests(BaseFeedback):

    def test_la_persona_puede_opinar_de_su_proyecto(self):
        f = self._sobre_el_proyecto()
        self.assertEqual(f.claridad_objetivo, 2)
        self.assertIn("alcance escrito", f.comentario)

    def test_sin_puntuar_la_claridad_no_se_guarda(self):
        with self.assertRaises(ValidationError):
            self._sobre_el_proyecto(claridad_objetivo=None)

    def test_la_claridad_va_del_1_al_5(self):
        with self.assertRaises(ValidationError):
            self._sobre_el_proyecto(claridad_objetivo=9)

    def test_nadie_opina_por_otro(self):
        otro_ing = User.objects.create_user("ing2_fb", "i2@test.com", "clave-larga-1")
        otro_ing.groups.add(Group.objects.get(name="Ingeniero"))
        Recurso.objects.create(
            nombre="Tercera", email="i2@test.com", banda="JR", usuario=otro_ing,
        )
        with self.assertRaises(PermissionDenied):
            self._sobre_el_proyecto(autor=otro_ing)


class QuienLeeQueTests(BaseFeedback):
    """La asimetria deliberada, que es la decision de diseno del modulo."""

    def test_la_persona_lee_lo_que_se_dijo_de_ella(self):
        """No hay expediente secreto: lo que se usa para evaluar a alguien tiene
        que poder ser leido por esa persona."""
        f = self._sobre_la_persona()
        self.assertIn(f, svc.feedback_visible(self.ing))

    def test_el_pm_lee_lo_que_el_mismo_observo(self):
        f = self._sobre_la_persona()
        self.assertIn(f, svc.feedback_visible(self.pm))

    def test_el_pm_NO_lee_lo_que_se_dijo_de_su_proyecto(self):
        """El nucleo del asunto. Si el jefe de proyecto pudiera leer esto con
        nombre y apellido, la respuesta honesta —«llevo tres dias esperando»— no
        se escribiria nunca, y el formulario recogeria cortesia."""
        f = self._sobre_el_proyecto()
        self.assertNotIn(f, svc.feedback_visible(self.pm))

    def test_quien_lo_escribio_si_lo_ve(self):
        f = self._sobre_el_proyecto()
        self.assertIn(f, svc.feedback_visible(self.ing))

    def test_el_admin_lo_ve_todo(self):
        """Es quien tiene que actuar: sin ver las dos direcciones no puede
        distinguir un problema de desempeno de uno de entrada."""
        sobre_persona = self._sobre_la_persona()
        sobre_proyecto = self._sobre_el_proyecto()
        visible = svc.feedback_visible(self.admin)
        self.assertIn(sobre_persona, visible)
        self.assertIn(sobre_proyecto, visible)

    def test_un_ingeniero_ajeno_no_ve_nada(self):
        self._sobre_la_persona()
        self._sobre_el_proyecto()
        ajeno = User.objects.create_user("aj_fb", "aj@test.com", "clave-larga-1")
        ajeno.groups.add(Group.objects.get(name="Ingeniero"))
        self.assertEqual(list(svc.feedback_visible(ajeno)), [])


class LaHuellaDeLaEdicionTests(BaseFeedback):
    """No se impide editar —una errata tiene que poder corregirse— pero que se
    note es lo que evita reescribir una observacion despues de que la lean."""

    def test_recien_escrito_no_figura_como_editado(self):
        f = self._sobre_la_persona()
        self.assertFalse(f.editado)

    def test_al_cambiarlo_mas_tarde_si(self):
        from datetime import timedelta

        f = self._sobre_la_persona()
        Feedback.objects.filter(pk=f.pk).update(
            updated_at=f.created_at + timedelta(hours=3)
        )
        f.refresh_from_db()
        self.assertTrue(f.editado)


class LaPantallaDeFeedbackTests(BaseFeedback):

    def test_se_abre_para_cualquiera(self):
        self.client.force_login(self.ing)
        resp = self.client.get(reverse("feedback"))
        self.assertEqual(resp.status_code, 200)

    def test_al_ingeniero_no_se_le_ofrece_observar(self):
        self.client.force_login(self.ing)
        resp = self.client.get(reverse("feedback"))
        self.assertFalse(resp.context["puede_observar"])

    def test_al_pm_si_en_la_de_gestionar(self):
        self.client.force_login(self.pm)
        resp = self.client.get(reverse("feedback-equipo"))
        self.assertTrue(resp.context["puede_observar"])
        self.assertIn(self.recurso, resp.context["recursos_observables"])

    def test_registrar_una_observacion(self):
        self.client.force_login(self.pm)
        self.client.post(reverse("feedback-equipo"), {
            "direccion": Feedback.PROYECTO_A_RECURSO,
            "recurso": self.recurso.pk,
            "proyecto": self.proyecto.pk,
            "conducta": "avisa cuando se atasca, y pronto",
            "impacto": "no se pierde el dia",
            "tipo": Feedback.FORTALEZA,
        })
        self.assertEqual(
            Feedback.objects.filter(direccion=Feedback.PROYECTO_A_RECURSO).count(), 1,
        )

    def test_registrar_una_opinion_del_proyecto(self):
        self.client.force_login(self.ing)
        self.client.post(reverse("feedback"), {
            "direccion": Feedback.RECURSO_A_PROYECTO,
            "proyecto": self.proyecto.pk,
            "claridad_objetivo": "2",
            "tuve_que_intuir": "si",
            "comentario": "bien la disponibilidad; mal el alcance por escrito",
            "que_ahorraria_tiempo": "un ejemplo de la respuesta esperada",
        })
        f = Feedback.objects.get(direccion=Feedback.RECURSO_A_PROYECTO)
        self.assertEqual(f.claridad_objetivo, 2)
        self.assertTrue(f.tuve_que_intuir)


class LoQueDaYLoQueRecibeTests(BaseFeedback):
    """La pantalla de gestion enfrenta las dos direcciones de una persona.

    Es lo que permite distinguir un problema de desempeno de uno de entrada, y
    obligar a saltar entre pantallas para compararlas es garantizar que nadie
    las compare.
    """

    def test_el_admin_ve_las_dos_columnas(self):
        self._sobre_la_persona()
        self._sobre_el_proyecto()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("feedback-equipo"), {"recurso": self.recurso.pk})

        self.assertEqual(len(resp.context["recibido"]), 1)
        self.assertEqual(len(resp.context["dado"]), 1)
        self.assertEqual(resp.context["elegido"], self.recurso)

    def test_el_pm_ve_lo_que_recibe_pero_no_lo_que_da(self):
        """La promesa con la que se pidio ese feedback fue que solo lo leyera el
        manager en Colombia. Ampliar el circulo la rompe."""
        self._sobre_la_persona()
        self._sobre_el_proyecto()
        self.client.force_login(self.pm)
        resp = self.client.get(reverse("feedback-equipo"), {"recurso": self.recurso.pk})

        self.assertEqual(len(resp.context["recibido"]), 1)
        self.assertEqual(resp.context["dado"], [])

    def test_en_registrar_no_se_puede_mirar_a_otro(self):
        """El filtro por persona es de la pantalla de gestion. Si funcionara
        aqui, cualquiera sondearia el expediente de sus companeros."""
        self._sobre_la_persona()
        self.client.force_login(self.ing)
        resp = self.client.get(reverse("feedback"), {"recurso": self.recurso.pk})
        self.assertIsNone(resp.context["elegido"])


class TranscribirLoQueDijoOtroTests(BaseFeedback):
    """El Admin copia lo que el jefe de proyecto mandó por chat.

    Los jefes de proyecto dicen «no tengo tiempo de entrar» y mandan la
    observación por Teams. Si no se puede transcribir, se pierde; y si el Admin
    la copia como suya, el registro miente sobre quién observó — y eso importa,
    porque al comparar evaluadores entre sí cuenta el juicio de quien vio la
    conducta, no la mano que la tecleó.
    """

    def test_el_admin_puede_transcribir(self):
        f = self._sobre_la_persona(autor=self.admin, en_nombre_de=self.pm)
        self.assertEqual(f.autor, self.admin)
        self.assertEqual(f.en_nombre_de, self.pm)
        self.assertEqual(f.observador, self.pm)
        self.assertTrue(f.transcrito)

    def test_sin_transcribir_el_observador_es_el_autor(self):
        f = self._sobre_la_persona()
        self.assertEqual(f.observador, self.pm)
        self.assertFalse(f.transcrito)

    def test_no_se_atribuye_a_quien_no_trabaja_con_esa_persona(self):
        """Una firma que esa persona no podria sostener no sirve de nada."""
        ajeno = User.objects.create_user("pm3_fb", "p3@test.com", "clave-larga-1")
        ajeno.groups.add(Group.objects.get(name="PM"))
        with self.assertRaises(ValidationError):
            self._sobre_la_persona(autor=self.admin, en_nombre_de=ajeno)

    def test_el_feedback_sobre_el_proyecto_no_se_transcribe(self):
        """Lo escribe quien lo vivio. Transcribirlo romperia justo la promesa
        con la que se pidio."""
        with self.assertRaises(ValidationError):
            self._sobre_el_proyecto(autor=self.admin, en_nombre_de=self.pm)

    def test_lo_ve_cualquiera_que_pueda_observar(self):
        """Un delegado tambien recibe por chat lo que el PM no entra a escribir;
        obligarle a pedirselo al Admin es anadir un salto para nada."""
        for usuario in (self.admin, self.pm):
            self.client.force_login(usuario)
            self.assertTrue(
                self.client.get(reverse("feedback")).context["posibles_observadores"],
                f"{usuario.username} deberia poder atribuir la observacion",
            )

    def test_solo_se_ofrece_a_quien_trabaja_con_esa_gente(self):
        """Ofrecer la lista entera invitaria a atribuir una observacion a alguien
        que no trabaja con esa persona, y esa firma no la sostiene nadie."""
        ajeno = User.objects.create_user("pm4_fb", "p4@test.com", "clave-larga-1")
        ajeno.groups.add(Group.objects.get(name="PM"))
        self.client.force_login(self.admin)
        ofrecidos = self.client.get(reverse("feedback")).context["posibles_observadores"]
        self.assertIn(self.pm, ofrecidos)
        self.assertNotIn(ajeno, ofrecidos)

    def test_al_ingeniero_no_se_le_ofrece_nada(self):
        """No puede observar, asi que tampoco transcribir."""
        self.client.force_login(self.ing)
        self.assertEqual(
            self.client.get(reverse("feedback")).context["posibles_observadores"], [],
        )

    def test_desde_la_pantalla(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("feedback"), {
            "direccion": Feedback.PROYECTO_A_RECURSO,
            "recurso": self.recurso.pk,
            "proyecto": self.proyecto.pk,
            "en_nombre_de": self.pm.pk,
            "conducta": "lo dijo Martin por Teams",
            "impacto": "se perdio medio dia",
            "tipo": Feedback.A_MEJORAR,
        })
        f = Feedback.objects.get(direccion=Feedback.PROYECTO_A_RECURSO)
        self.assertEqual(f.en_nombre_de, self.pm)
        self.assertEqual(f.autor, self.admin)


class QueSeHizoConEsoTests(BaseFeedback):
    """La mitad que faltaba: que paso DESPUES de leer las senales.

    Un recurso con tres observaciones a mejorar y ninguna conversacion
    registrada no es un problema de la persona: es un problema de seguimiento.
    Sin esta tabla esa distincion no se puede hacer, porque solo se guardaba una
    de las dos mitades.
    """

    def _accion(self, autor=None, **extra):
        datos = dict(tipo="CONVERSACION", texto="hablamos el martes, se compromete a avisar antes")
        datos.update(extra)
        return svc.registrar_accion(
            recurso=self.recurso, autor=autor or self.admin, **datos
        )

    def test_el_admin_registra_lo_que_hizo(self):
        a = self._accion()
        self.assertEqual(a.recurso, self.recurso)
        self.assertEqual(a.autor, self.admin)

    def test_sin_texto_no_se_guarda(self):
        """Una accion sin contenido no le sirve a quien herede el seguimiento."""
        with self.assertRaises(ValidationError):
            self._accion(texto="   ")

    def test_revisado_sin_accion_tambien_cuenta(self):
        """Obligar a que toda senal termine en una accion fabrica acciones de
        mentira. Una decision descartada a conciencia es informacion."""
        a = self._accion(tipo="SIN_ACCION", texto="esta semana esta en formacion, es normal")
        self.assertEqual(a.tipo, "SIN_ACCION")

    def test_se_puede_atar_a_una_observacion(self):
        f = self._sobre_la_persona()
        a = self._accion(feedback=f)
        self.assertEqual(a.feedback, f)

    def test_pero_no_a_la_de_otra_persona(self):
        otro = Recurso.objects.create(nombre="Ajena", email="aja@test.com", banda="JR")
        f = svc.registrar_feedback(
            recurso=otro, autor=self.admin, direccion=Feedback.PROYECTO_A_RECURSO,
            conducta="algo", impacto="algo", tipo=Feedback.FORTALEZA,
        )
        with self.assertRaises(ValidationError):
            self._accion(feedback=f)

    def test_un_pm_no_registra_acciones(self):
        """Seria un segundo canal de feedback, y ya hay uno."""
        with self.assertRaises(PermissionDenied):
            self._accion(autor=self.pm)

    def test_el_historial_es_solo_de_quien_lleva_el_seguimiento(self):
        """Son notas de gestion. Publicarlas convertiria cada nota en un mensaje
        dirigido, que es otra cosa y se escribe distinto."""
        self._accion()
        self.assertEqual(svc.acciones_de(self.admin, self.recurso).count(), 1)
        self.assertEqual(svc.acciones_de(self.ing, self.recurso).count(), 0)
        self.assertEqual(svc.acciones_de(self.pm, self.recurso).count(), 0)

    def test_desde_la_pantalla(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("feedback-equipo"), {
            "accion": "seguimiento",
            "recurso": self.recurso.pk,
            "tipo": "ESCALADO",
            "texto": "escalado a Martin por escrito, citando las fechas",
        })
        self.assertEqual(svc.acciones_de(self.admin, self.recurso).count(), 1)


class ElProyectoSigueALaPersonaTests(BaseFeedback):
    """El desplegable de proyecto se llena con los proyectos de quien se elige.

    Observar a alguien «en» un proyecto donde nunca estuvo produce una
    observacion que despues no sostiene nadie, asi que la lista se acota. Va al
    navegador en un `json_script` para que reaccione sin recargar.

    Fallo tres veces seguidas por un motivo que no daba ningun error: la vista
    serializaba el mapa con `json.dumps` y la plantilla lo volvia a serializar
    con `|json_script`. Doble codificado, el `JSON.parse` del navegador devuelve
    una CADENA en vez de un objeto, `datos[id]` es `undefined`, y el desplegable
    se queda vacio en silencio. Estas pruebas miran lo que de verdad llega al
    navegador, no lo que la vista cree que manda.
    """

    def setUp(self):
        super().setUp()
        self.delegada = User.objects.create_user("del_map", "dm@test.com", "clave-larga-1")
        self.delegada.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])
        self.proyecto.aprobador_delegado = self.delegada
        self.proyecto.save(update_fields=["aprobador_delegado"])

    def _mapa_del_navegador(self, usuario):
        """Lo que sale del `json_script`, parseado como lo haria el navegador."""
        import json
        import re

        self.client.force_login(usuario)
        html = self.client.get(reverse("feedback")).content.decode()
        m = re.search(
            r'<script id="proyectos-por-recurso"[^>]*>(.*?)</script>', html, re.S,
        )
        self.assertIsNotNone(m, "el mapa no llego al navegador")
        return json.loads(m.group(1))

    def test_llega_como_objeto_y_no_como_cadena(self):
        """La prueba que faltaba. Con doble codificacion esto es un `str`."""
        mapa = self._mapa_del_navegador(self.pm)
        self.assertIsInstance(
            mapa, dict,
            "el mapa llega doble-codificado: JSON.parse devolvera una cadena y "
            "el desplegable se quedara vacio sin ningun error",
        )

    def test_trae_el_proyecto_de_esa_persona(self):
        mapa = self._mapa_del_navegador(self.pm)
        entradas = mapa[str(self.recurso.pk)]
        self.assertEqual(entradas[0]["id"], self.proyecto.pk)
        self.assertIn(self.proyecto.codigo, entradas[0]["texto"])

    def test_un_delegado_tambien_lo_recibe(self):
        """El caso que lo destapo: un aprobador delegado, con rol Ingeniero."""
        mapa = self._mapa_del_navegador(self.delegada)
        self.assertIn(str(self.recurso.pk), mapa)

    def test_cada_entrada_trae_lo_que_el_navegador_pinta(self):
        """Sin `texto` el desplegable pinta una opcion en blanco, que es el otro
        sintoma que se vio en pantalla."""
        mapa = self._mapa_del_navegador(self.pm)
        for entradas in mapa.values():
            for e in entradas:
                self.assertTrue(e.get("texto"), f"opcion sin etiqueta: {e}")
                self.assertTrue(e.get("id"))

    def test_no_trae_proyectos_fuera_del_alcance(self):
        otro_pm = User.objects.create_user("pm5_map", "p5@test.com", "clave-larga-1")
        otro_pm.groups.add(Group.objects.get(name="PM"))
        ajeno = Proyecto.objects.create(
            codigo="MAP-X", nombre="Ajeno", pm=otro_pm,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
        )
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=ajeno,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
            horas_totales=10, intensidad_diaria=2,
            estado="APROBADA", solicitada_por=otro_pm,
        )
        codigos = [
            e["texto"] for entradas in self._mapa_del_navegador(self.pm).values()
            for e in entradas
        ]
        self.assertFalse(
            [c for c in codigos if "MAP-X" in c],
            "un PM no puede observar a alguien en un proyecto que no dirige",
        )
