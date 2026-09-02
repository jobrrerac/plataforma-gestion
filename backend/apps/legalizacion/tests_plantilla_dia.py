"""La pantalla de registro de horas no imprime basura ni esconde el límite.

El comentario que se escapaba: `{# ... #}` de Django solo admite UNA línea. Si
se parte en dos, deja de ser un comentario y el texto sale impreso en la página.
Estaba pasando en el bloque del día aprobado —los usuarios veían un párrafo
sobre `default` y campos nulos encima de «Aprobado por …»— y había una nota en
`login.html` advirtiendo justo de esto.

Se prueba con una comprobación general, no solo contra ese texto: cualquier
`{#` o `{%` que llegue al HTML renderizado es un error, venga de donde venga.
"""

import re
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Recurso
from apps.legalizacion.models import DiaLegalizado


class PlantillaDiaTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user("ing", "ing@test.com", "clave-larga-123")
        self.recurso = Recurso.objects.create(
            nombre="Guzman-Mejia Daniel-Fernando", email="ing@test.com",
            banda="SR", usuario=self.usuario,
        )
        self.client.force_login(self.usuario)

    def _html(self, fecha=None):
        url = reverse("horas")
        if fecha:
            url += f"?fecha={fecha:%Y-%m-%d}"
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.content.decode()

    def test_no_se_imprime_ninguna_etiqueta_de_plantilla(self):
        html = self._html()
        fugas = re.findall(r"\{#.{0,60}|\{%.{0,40}", html)
        self.assertEqual(fugas, [], f"llegaron etiquetas sin procesar al HTML: {fugas}")

    def test_un_dia_aprobado_no_filtra_el_comentario(self):
        """El caso concreto donde estaba: el bloque del dia ya aceptado."""
        DiaLegalizado.objects.create(
            recurso=self.recurso, fecha=date(2026, 8, 28),
            estado=DiaLegalizado.APROBADO,
            total_horas=Decimal("8.0"), jornada_esperada=Decimal("8.0"),
        )
        html = self._html(date(2026, 8, 28))
        self.assertNotIn("{#", html)
        self.assertNotIn("reventaba la", html)
        self.assertIn("Aprobado", html)

    def test_el_formulario_dice_el_limite_y_que_se_evalua(self):
        """Un campo que se corta en seco sin avisar hace que la gente escriba
        peor, no mas corto; y avisar de que se revisa mejora lo que se escribe."""
        html = self._html(date(2026, 8, 28))
        self.assertIn('id="f-detalle-cuenta"', html)
        self.assertIn("0 / 300", html)
        self.assertIn("Son 300 caracteres", html)
        self.assertIn("alcance para aprobar esas horas", html)
