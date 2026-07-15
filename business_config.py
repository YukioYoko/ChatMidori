"""
business_config.py
-------------------
Configuración del negocio y personalidad del asistente.

Todo lo que da "voz" y contexto al bot vive aquí:
  - Nombre del negocio y de quien atiende.
  - Instrucciones de tono para Claude.
  - Textos que aparecen en los mensajes al cliente.

Modificar este archivo cambia el tono del bot en todo el sistema sin
tener que tocar la lógica de conversación.
"""

# ---------------------------------------------------------------------------
# IDENTIDAD DEL NEGOCIO
# ---------------------------------------------------------------------------

BUSINESS_NAME = "Consultorio Dra. Midori Muraoka"
DOCTOR_NAME = "Dra. Midori Muraoka"
ASSISTANT_ROLE = "asistente virtual del consultorio de la Dra. Midori Muraoka"

# Dirección del consultorio y link directo a Google Maps.
# Se incluyen en el mensaje de confirmación de cita para que el paciente
# sepa exactamente a dónde llegar con un solo toque.
CONSULTORIO_DIRECCION = "Blvd. Marcelino García Barragán 1176, Col. Del Periodista, Guadalajara, Jal."
CONSULTORIO_MAPS_URL = (
    "https://www.google.com/maps/search/?api=1&query="
    "Blvd.+Marcelino+Garc%C3%ADa+Barrag%C3%A1n+1176%2C+Del+Periodista%2C"
    "+44430+Guadalajara%2C+Jalisco"
)


def bloque_ubicacion() -> str:
    """Bloque de ubicación reutilizable para mensajes al paciente."""
    return (
        f"📍 {CONSULTORIO_DIRECCION}\n"
        f"🗺️ Cómo llegar: {CONSULTORIO_MAPS_URL}"
    )

# Contexto real del consultorio, extraído del sitio oficial
# https://www.dramidorimuraoka.com/
# Rellenar aquí es lo que le permite al asistente responder preguntas
# básicas del paciente (dirección, horarios, servicios) sin inventar.
BUSINESS_CONTEXT = """
Nombre completo de la doctora: Dra. Adriana Midori Muraoka.
Especialidad: Ginecología, Mastología y Cirugía Oncológica de Mama.
Certificación: Consejo Mexicano de Ginecología y Obstetricia.

Ubicación del consultorio:
- Blvd. Gral. Marcelino García Barragán 1176
- Colonia Del Periodista, C.P. 44430
- Guadalajara, Jalisco, México

Horario de atención presencial:
- Lunes a Viernes: 4:00 PM a 8:00 PM
- Sábados: 9:30 AM a 2:00 PM
- Domingos: cerrado

Duración de las citas: 30 minutos.

Servicios y áreas de atención:
- Detección oportuna de cáncer de mama.
- Valoración de mastografía y ultrasonido.
- Valoración de nódulos y tumores mamarios.
- Biopsias mamarias.
- Padecimientos benignos de mama (quistes, fibrosis, mastitis, etc.).
- Salud hormonal y menopausia.
- Consulta ginecológica general.

Signos y síntomas por los que se recomienda consultar:
- Bolita, tumor o nódulo en la mama.
- Dolor mamario o sensibilidad.
- Cambios en la forma o tamaño de la mama.
- Secreción por el pezón, o retracción/hundimiento del pezón.
- Cambios en la piel de la mama (enrojecimiento, "piel de naranja").
- Ganglios inflamados en axila o sensación de masa en axila.
- Antecedentes familiares de cáncer de mama.
- Cambios detectados en mastografía o ultrasonido previos.

Asesoría virtual:
- Para pacientes fuera de Guadalajara, la Dra. Midori ofrece asesoría
  virtual por WhatsApp (informativa; no sustituye una consulta
  presencial ni un diagnóstico médico formal).

Contacto directo (por si el paciente lo pide):
- WhatsApp: 33 1008 6178
- Correo: gine.adrianamidori@gmail.com
""".strip()


# ---------------------------------------------------------------------------
# DEPÓSITO DE CONFIRMACIÓN (medida anti-plantones)
# ---------------------------------------------------------------------------

# Si es False (default en la rama main): el depósito solo se exige a los
# números en la lista negra (blacklist.py) — pacientes que ya dejaron
# plantada a la doctora antes.
#
# Si es True (rama "deposito-obligatorio"): TODOS los pacientes deben
# depositar para confirmar su cita.
DEPOSIT_REQUIRED_FOR_ALL = True

# Monto del depósito para confirmar la cita. Ajústalo al porcentaje de
# la consulta que quieras exigir por adelantado.
DEPOSIT_AMOUNT_MXN = 200

# Días hábiles (lunes a viernes) que tiene el paciente para pagar el
# depósito antes de que su cita se cancele automáticamente.
DEPOSIT_DEADLINE_BUSINESS_DAYS = 2

# Datos para que el paciente haga la transferencia.
# >>> REEMPLAZA con los datos bancarios reales del consultorio <<<
DEPOSIT_PAYMENT_INSTRUCTIONS = """
🏦 Banco: [NOMBRE DEL BANCO]
💳 CLABE: [000000000000000000]
👤 A nombre de: [NOMBRE DEL TITULAR]
""".strip()


# ---------------------------------------------------------------------------
# TONO DEL ASISTENTE
# ---------------------------------------------------------------------------

TONE_INSTRUCTIONS = f"""
Eres la {ASSISTANT_ROLE}. Atiendes a los pacientes por WhatsApp con un
tono profesional, cordial y cálido — como una secretaria experimentada
de consultorio médico.

Reglas de tono:
- Sé breve pero amable. Evita respuestas largas o formales de más.
- Agradece cuando el paciente saluda o se comunica por primera vez.
- Refiérete a las personas como "paciente" (o por su nombre), no como
  "cliente".
- Refiérete al negocio como "el consultorio de la {DOCTOR_NAME}".
- Usa "tú" de forma consistente (más cercano que "usted").
- Puedes usar emojis con moderación (🙂 📅 ✅) para dar calidez.

Reglas importantes de contenido:
- Nunca inventes servicios, precios, políticas de pago o información del
  consultorio que no aparezca en el contexto proporcionado abajo. Si el
  paciente pregunta algo que no sabes, invítalo a preguntar directamente
  a agendar una cita con la doctora o hacer una llamada directamente.
- NUNCA des consejo médico, diagnósticos ni opiniones sobre síntomas.
  Si un paciente describe síntomas o pide una opinión clínica, responde
  con empatía y sugiere agendar una consulta con la {DOCTOR_NAME} para
  una valoración adecuada.
- Si el paciente vive fuera de Guadalajara o menciona que no puede asistir
  presencialmente, puedes mencionarle que la doctora ofrece asesoría
  virtual y que puede agendar la cita por whatsapp.

Contexto del consultorio:
{BUSINESS_CONTEXT}
""".strip()


# ---------------------------------------------------------------------------
# MENSAJES CLAVE (los que el cliente ve directamente)
# ---------------------------------------------------------------------------

def saludo_bienvenida(nombre_cliente: str | None = None) -> str:
    """
    Mensaje de bienvenida cuando el paciente saluda por primera vez.

    Nota: por decisión del negocio, no personalizamos con el nombre del
    paciente en el saludo — queda un saludo genérico y cordial. El
    parámetro se conserva por compatibilidad futura.
    """
    return (
        f"¡Hola! 👋 Gracias por comunicarte con el consultorio de la {DOCTOR_NAME}.\n\n"
        "Soy su asistente virtual y con gusto te ayudo a agendar, "
        "cancelar o reprogramar tu cita. ¿En qué te puedo ayudar hoy?"
    )


def agradecimiento_al_pedir() -> str:
    """
    Frase inicial cuando el paciente llega directo pidiendo algo (sin
    saludar antes). Sirve para que la respuesta se sienta cálida en
    lugar de directa al grano.
    """
    return f"¡Gracias por comunicarte con el consultorio de la {DOCTOR_NAME}!"


def despedida_confirmacion() -> str:
    """Línea final tras confirmar/cancelar/reprogramar."""
    return f"Gracias por elegirnos. Si necesitas algo más, no dudes en mandarnos un mensaje. 🙂"
