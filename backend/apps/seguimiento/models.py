"""Seguimiento de la entrega: qué frena a alguien y qué se le dice.

Existe por una razón concreta. Un recurso rindió por debajo de lo esperado
durante un mes; ni él ni el jefe de proyecto lo dijeron, y la señal llegó tarde
para corregir. La lectura fácil es que faltó seguimiento; la correcta es que
**no había nada que produjera una señal**: las tareas se asignaban de palabra y
día a día, las dudas se resolvían según la disponibilidad de quien las
resolviera, y no quedaba ningún registro que envejeciera y molestara cuando algo
llevaba días parado.

Este módulo son esos dos registros que envejecen:

- `Bloqueante` — qué frena a alguien, desde cuándo y a quién le toca resolverlo.
- `Feedback` — qué se observó, en las **dos** direcciones.

Las dos direcciones no son un detalle. Todo lo que se registre sobre el recurso
tiene su equivalente sobre el proyecto, porque un instrumento que solo mira
hacia un lado produce, mes tras mes, evidencia contra el lado que no tiene voz.
Y ese lado, aquí, son personas junior recién egresadas cuya principal dificultad
documentada no es lo que saben, sino que reciben tareas sin alcance escrito.
"""

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import Proyecto, Recurso, SoftDeleteModel

# Cuánto puede estar abierto un bloqueante antes de que sea un problema de
# alguien más. Dos días hábiles no: 48 horas. Un bloqueo que nace un jueves por
# la tarde y sigue el lunes ya costó cuatro días de calendario, y lo que se
# quiere medir es el tiempo real que alguien estuvo esperando.
HORAS_PARA_ESCALAR = 48


class Bloqueante(SoftDeleteModel):
    """Algo que impide avanzar, con dueño y con edad.

    La pieza central del módulo, y la que habría detectado el caso que lo
    motivó. Un bloqueante no es una queja: es una conducta que se pide y que se
    reconoce. **Levantar la mano se premia.** En el momento en que reportar un
    bloqueo se vea mal, nadie reporta ninguno y volvemos exactamente al punto de
    partida, que era el silencio.

    Lo que lo hace útil es `resuelto_en`. Sin esa fecha hay una lista de quejas;
    con ella hay una métrica —la diferencia entre las dos fechas es el tiempo de
    respuesta de quien tenía que desbloquear— y no hizo falta pedirle a nadie del
    lado cliente que registrara nada. Ese es el punto: **ningún indicador del
    proyecto depende de que el proyecto colabore.**
    """

    recurso = models.ForeignKey(
        Recurso, on_delete=models.PROTECT, related_name="bloqueantes",
    )
    # Puede no haberlo: alguien en formación o entre asignaciones también se
    # bloquea, y esos casos son justo los que más se pierden de vista.
    proyecto = models.ForeignKey(
        Proyecto, on_delete=models.PROTECT, related_name="bloqueantes",
        null=True, blank=True,
    )

    necesito = models.CharField(
        max_length=300,
        verbose_name="Qué te está bloqueando",
        help_text="En una frase. Lo va a leer alguien que no tiene tu contexto.",
    )

    # A quién le toca desbloquear. Se pregunta por ROL y no por persona.
    #
    # La primera versión pedía elegir a alguien de una lista, y eso es pedirle a
    # un junior recién llegado un dato que muchas veces no tiene: sabe que espera
    # «al jefe de proyecto» o «a alguien de accesos», no cómo se llama. Un
    # formulario que exige lo que no se sabe consigue que no se rellene.
    #
    # El rol además agrega bien —«los jefes de proyecto tardan 60 h de media»— y
    # no se fragmenta como el texto libre, donde «Álvaro», «alvaro» y «Alvaro O.»
    # cuentan como tres.
    ROL_CHOICES = [
        ("PM", "Jefe de proyecto"),
        ("LIDER", "Líder de equipo"),
        ("COMPANERO", "Un compañero del equipo"),
        ("CLIENTE", "Alguien del cliente"),
        ("ACCESOS", "Accesos o soporte técnico"),
        ("OTRO", "Otra persona"),
    ]

    # Cómo se lee cada rol después de «esperando a». La alerta es el texto más
    # leído del módulo; que suene a castellano no es un lujo.
    ROL_FRASE = {
        "PM": "el jefe de proyecto",
        "LIDER": "el líder de equipo",
        "COMPANERO": "un compañero del equipo",
        "CLIENTE": "alguien del cliente",
        "ACCESOS": "accesos o soporte técnico",
        "OTRO": "otra persona",
    }

    rol_que_resuelve = models.CharField(
        max_length=12, choices=ROL_CHOICES, default="OTRO",
        verbose_name="Rol que debe resolverlo",
    )
    bloquea_nombre = models.CharField(
        max_length=120, blank=True,
        verbose_name="Nombre",
        help_text="Opcional. Si sabes quién es en concreto, ayuda a reclamarlo.",
    )

    # Se rellena solo cuando se puede deducir —rol «jefe de proyecto» sobre un
    # proyecto que tiene PM—, nunca preguntando. Es lo que permite seguir
    # agregando por persona en el caso más frecuente sin cargarle al recurso una
    # pregunta más.
    bloquea_usuario = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="bloqueantes_a_su_cargo",
        null=True, blank=True, verbose_name="Persona concreta (deducida)",
    )

    mientras_tanto = models.CharField(
        max_length=300, blank=True,
        verbose_name="Qué haces mientras",
        help_text="Opcional. Sirve para saber si el bloqueo te dejó parado del todo.",
    )

    creado_en = models.DateTimeField(auto_now_add=True)
    creado_por = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="bloqueantes_reportados",
    )

    resuelto_en = models.DateTimeField(null=True, blank=True)
    resuelto_por = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="bloqueantes_resueltos",
        null=True, blank=True,
    )
    como_se_resolvio = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["resuelto_en", "creado_en"]
        verbose_name = "Bloqueante"
        verbose_name_plural = "Bloqueantes"
        indexes = [
            # Las dos consultas que se hacen todo el tiempo: los abiertos de una
            # persona, y todos los abiertos para el panel de alertas.
            models.Index(fields=["recurso", "resuelto_en"]),
            models.Index(fields=["resuelto_en", "creado_en"]),
        ]

    def __str__(self):
        estado = "abierto" if self.abierto else "resuelto"
        return f"{self.recurso.nombre}: {self.necesito[:50]} ({estado})"

    def clean(self):
        if not self.rol_que_resuelve:
            raise ValidationError(
                "Di a quién le toca resolverlo. Un bloqueante sin dueño no lo "
                "desatasca nadie."
            )

    @property
    def abierto(self) -> bool:
        return self.resuelto_en is None

    @property
    def bloqueador(self) -> str:
        """A quién se espera, con el mayor detalle que se tenga.

        El nombre cuando se conoce; el rol cuando no. Nunca vacío: siempre hay
        alguien a quien reclamarle, aunque sea genérico.
        """
        if self.bloquea_usuario_id:
            return self.bloquea_usuario.get_full_name() or self.bloquea_usuario.username
        if self.bloquea_nombre:
            return self.bloquea_nombre
        return self.ROL_FRASE.get(self.rol_que_resuelve, "otra persona")

    @property
    def horas_abierto(self) -> float:
        """Horas que lleva —o que llevó— esperando."""
        fin = self.resuelto_en or timezone.now()
        # `creado_en` es auto_now_add: en un objeto aún sin guardar no existe.
        if self.creado_en is None:
            return 0.0
        return round((fin - self.creado_en).total_seconds() / 3600, 1)

    @property
    def vencido(self) -> bool:
        """Abierto y por encima del plazo. Es lo que dispara la alerta."""
        return self.abierto and self.horas_abierto >= HORAS_PARA_ESCALAR


class Feedback(SoftDeleteModel):
    """Una observación fechada, en cualquiera de las dos direcciones.

    Un solo modelo para las dos direcciones a propósito: lo que da valor a esto
    es poder leerlas **enfrentadas** en la misma línea de tiempo. Con dos
    modelos, cada pantalla que las muestre junta tendría que mezclarlas a mano y
    tarde o temprano una de las dos se quedaría fuera de alguna.

    Reglas que sostiene el modelo:

    - **La observación es obligatoria; la nota, no.** Un registro sin conducta
      observada no se guarda. Una observación con fecha se puede contrastar y
      rebatir; un «3 sobre 5» no se puede ni defender ni discutir.
    - **El autor siempre queda.** Una calificación sin autor no se puede
      comparar contra las que pone esa misma persona al resto, que es como se
      detecta a quien califica bajo a todo el mundo.
    - **No hay expediente secreto.** Lo que se registra sobre alguien lo puede
      ver esa persona.

    No es append-only, a diferencia de `LogAuditoria`: una errata en una
    observación tiene que poder corregirse. Lo que sí queda es la huella —
    `created_at` y `updated_at` son distintos en cuanto alguien edita— y la
    pantalla lo muestra.
    """

    PROYECTO_A_RECURSO = "P2R"
    RECURSO_A_PROYECTO = "R2P"
    DIRECCION_CHOICES = [
        (PROYECTO_A_RECURSO, "Del proyecto hacia la persona"),
        (RECURSO_A_PROYECTO, "De la persona hacia el proyecto"),
    ]

    SEMANAL = "SEMANAL"
    CIERRE = "CIERRE"
    MOMENTO_CHOICES = [
        (SEMANAL, "Seguimiento semanal"),
        (CIERRE, "Cierre de asignación"),
    ]

    FORTALEZA = "FORTALEZA"
    A_MEJORAR = "A_MEJORAR"
    TIPO_CHOICES = [
        (FORTALEZA, "Fortaleza"),
        (A_MEJORAR, "A mejorar"),
    ]

    # Deliberadamente pocas y anchas. Una lista larga de dimensiones invita a
    # rellenarlas todas cada semana, que es como se consigue que nadie escriba
    # nada real en ninguna.
    DIMENSION_CHOICES = [
        ("COMUNICACION", "Comunicación"),
        ("AUTONOMIA", "Autonomía"),
        ("TECNICO", "Conocimiento técnico"),
        ("PLANIFICACION", "Planificación y estimación"),
    ]

    # ── Esqueleto común a las dos direcciones ────────────────────────────────
    recurso = models.ForeignKey(
        Recurso, on_delete=models.PROTECT, related_name="feedbacks",
        help_text="La persona sobre la que, o desde la que, se observa.",
    )
    proyecto = models.ForeignKey(
        Proyecto, on_delete=models.PROTECT, related_name="feedbacks",
        null=True, blank=True,
    )
    autor = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="feedbacks_escritos",
        help_text="Quien lo escribió en la aplicación.",
    )
    # Quien hizo la observación, cuando no es quien la teclea.
    #
    # Los jefes de proyecto dicen «no tengo tiempo de entrar» y mandan la
    # observación por chat. Si el Admin la copia como suya, el registro miente
    # sobre quién observó; y si no se copia, se pierde. Se guardan las dos
    # personas: quien lo vio y quien lo transcribió.
    #
    # Importa para leer los datos después: al comparar evaluadores entre sí
    # —que es como se detecta a quien califica bajo a todo el mundo— cuenta el
    # juicio de quien observó, no la mano que lo escribió.
    en_nombre_de = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="feedbacks_transcritos",
        null=True, blank=True, verbose_name="Observado por",
    )
    direccion = models.CharField(max_length=3, choices=DIRECCION_CHOICES)
    momento = models.CharField(max_length=10, choices=MOMENTO_CHOICES, default=SEMANAL)
    # Cuándo pasó, que no siempre es cuándo se escribe. Se registra el viernes lo
    # que se observó el martes, y la fecha que importa para el historial es la
    # del martes.
    fecha_observacion = models.DateField(default=timezone.localdate)

    # ── Del proyecto hacia la persona ────────────────────────────────────────
    situacion = models.CharField(
        max_length=300, blank=True,
        help_text="Cuándo y en qué contexto.",
    )
    conducta = models.TextField(
        blank=True,
        verbose_name="Conducta observada",
        help_text="Qué hizo o dejó de hacer. Hechos, no adjetivos.",
    )
    impacto = models.TextField(
        blank=True,
        help_text="Qué consecuencia tuvo. Sin esto no se entiende por qué importa.",
    )
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, blank=True)
    dimension = models.CharField(max_length=20, choices=DIMENSION_CHOICES, blank=True)

    # ── De la persona hacia el proyecto ──────────────────────────────────────
    #
    # Esta mitad es la que hoy no existe en ninguna parte y la que más
    # información nueva aporta: es la única que puede decir que el objetivo llegó
    # a medias o que nadie contestó en tres días.
    claridad_objetivo = models.PositiveSmallIntegerField(
        null=True, blank=True,
        verbose_name="Claridad del objetivo",
        help_text="1 = no supe qué se esperaba · 5 = clarísimo desde el principio.",
    )
    # No se pregunta cuántas horas tardaron en responder: eso lo mide el
    # bloqueante con precisión de reloj y sin depender de la memoria de nadie.
    # Preguntarlo aquí otra vez sería pedir a mano un dato peor.
    tuve_que_intuir = models.BooleanField(null=True, blank=True)
    # El campo abierto, y el que más vale. Los desplegables dan la tendencia; esto
    # da el motivo, que es lo que se puede llevar a una conversación con el
    # proyecto. Se pregunta por las dos caras a propósito: un formulario que solo
    # pide quejas recoge quejas, y deja de leerse.
    comentario = models.TextField(
        blank=True,
        verbose_name="Tu feedback para el proyecto",
        help_text="Qué estuvo bien y qué puede mejorar.",
    )
    cambio_alcance = models.BooleanField(null=True, blank=True)
    veces_cambio_alcance = models.PositiveSmallIntegerField(null=True, blank=True)
    que_ahorraria_tiempo = models.TextField(
        blank=True,
        verbose_name="Qué me habría ahorrado tiempo",
    )

    class Meta:
        ordering = ["-fecha_observacion", "-id"]
        verbose_name = "Feedback"
        verbose_name_plural = "Feedback"
        indexes = [
            models.Index(fields=["recurso", "-fecha_observacion"]),
            models.Index(fields=["direccion", "-fecha_observacion"]),
        ]

    def __str__(self):
        return f"{self.get_direccion_display()} · {self.recurso.nombre} · {self.fecha_observacion:%d/%m/%Y}"

    @property
    def observador(self):
        """Quien hizo la observación, transcrita o no."""
        return self.en_nombre_de or self.autor

    @property
    def transcrito(self) -> bool:
        return bool(self.en_nombre_de_id and self.en_nombre_de_id != self.autor_id)

    @property
    def editado(self) -> bool:
        """Si se tocó después de escribirlo. La pantalla lo dice.

        No se impide editar —una errata tiene que poder corregirse— pero que se
        note es lo que evita que una observación se reescriba en silencio
        después de que alguien la haya leído.
        """
        if not self.created_at or not self.updated_at:
            return False
        return (self.updated_at - self.created_at).total_seconds() > 60

    def clean(self):
        if self.direccion == self.PROYECTO_A_RECURSO:
            if not self.conducta.strip():
                raise ValidationError({
                    "conducta": "Describe qué hiciste u observaste. Sin conducta "
                                "observada esto es una opinión, no un feedback.",
                })
            if not self.tipo:
                raise ValidationError({"tipo": "Di si es una fortaleza o algo a mejorar."})
        elif self.direccion == self.RECURSO_A_PROYECTO:
            if self.claridad_objetivo is None:
                raise ValidationError({
                    "claridad_objetivo": "Puntúa del 1 al 5 qué tan claro estaba el objetivo.",
                })
            if not (1 <= self.claridad_objetivo <= 5):
                raise ValidationError({"claridad_objetivo": "Del 1 al 5."})


class AccionDeSeguimiento(SoftDeleteModel):
    """Qué hizo el manager con lo que leyó. Fechado.

    Sin esto, el módulo recoge señales y no dice qué pasó después. Y ese
    "después" es la mitad que importa: un recurso con tres observaciones a
    mejorar y ninguna conversación registrada no es un problema de la persona,
    es un problema de seguimiento — y hasta ahora esa distinción no se podía
    hacer porque solo se guardaba una de las dos mitades.

    Es además la respuesta a la pregunta incómoda. «¿Por qué no hiciste
    seguimiento uno a uno con los 27?» tiene una mala respuesta, que es
    intentarlo, y una buena: *estas son las situaciones que levantó el sistema,
    esto hice con cada una, y aquí están las fechas.* Un registro de excepciones
    atendidas es más defendible que una agenda llena.

    Se guarda también «no hice nada, y por esto»: una acción descartada a
    conciencia es información, y obligar a que toda señal termine en una acción
    fabrica acciones de mentira.
    """

    CONVERSACION = "CONVERSACION"
    ESCALADO = "ESCALADO"
    FORMACION = "FORMACION"
    CAMBIO = "CAMBIO"
    SIN_ACCION = "SIN_ACCION"
    TIPO_CHOICES = [
        (CONVERSACION, "Conversación con la persona"),
        (ESCALADO, "Escalado al proyecto"),
        (FORMACION, "Formación o acompañamiento"),
        (CAMBIO, "Cambio de asignación o de alcance"),
        (SIN_ACCION, "Revisado, sin acción por ahora"),
    ]

    recurso = models.ForeignKey(
        Recurso, on_delete=models.PROTECT, related_name="acciones_seguimiento",
    )
    autor = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="acciones_de_seguimiento",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    tipo = models.CharField(max_length=14, choices=TIPO_CHOICES, default=CONVERSACION)
    texto = models.TextField(
        verbose_name="Qué hiciste",
        help_text="Qué decidiste y por qué. Lo va a leer quien herede este seguimiento.",
    )
    # A qué observación responde, si responde a una concreta. Opcional porque la
    # mayoría de las decisiones salen de leer varias juntas, no de una sola.
    feedback = models.ForeignKey(
        Feedback, on_delete=models.PROTECT, related_name="acciones",
        null=True, blank=True, verbose_name="A raíz de",
    )

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Acción de seguimiento"
        verbose_name_plural = "Acciones de seguimiento"
        indexes = [models.Index(fields=["recurso", "-creado_en"])]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.recurso.nombre} · {self.creado_en:%d/%m/%Y}"
