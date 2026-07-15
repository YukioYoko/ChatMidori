"""
nlu_service.py
---------------
Capa de interpretación de lenguaje natural (NLU) usando la API de Claude.

Toma el texto libre que el cliente escribió por WhatsApp y lo convierte en
datos estructurados (intención, fecha, hora, nombre) que el resto del
sistema puede usar de forma determinista.

Usa Claude Haiku 4.5: es el modelo más económico de Anthropic y de sobra
para una tarea de clasificación/extracción como esta (no necesitamos
razonamiento profundo, solo entender frases como "mañana en la tarde").
"""

import os
import json
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import anthropic

import business_config

logger = logging.getLogger("nlu_service")

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

# -----------------------------------------------------------------------
# >>> AQUÍ VA TU API KEY DE ANTHROPIC <<<
#
# 1. Crea una cuenta en https://console.anthropic.com
# 2. Genera una API key en la sección "API Keys".
# 3. NUNCA la pongas directamente en el código. Configúrala como variable
#    de entorno antes de correr el bot:
#
#       export ANTHROPIC_API_KEY="sk-ant-..."
#
#    O bien, colócala en un archivo .env y cárgalo con python-dotenv.
# -----------------------------------------------------------------------
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Modelo económico: ideal para clasificación y extracción de datos.
MODEL_NAME = "claude-haiku-4-5-20251001"

# Debe coincidir con la zona horaria usada en rules_engine.py / calendar_service.py
TIMEZONE = ZoneInfo("America/Mexico_City")

# Intenciones válidas que el modelo puede devolver. Mantenerlas en una
# lista cerrada hace que el resto del sistema (conversation_manager.py)
# pueda usar un switch/if determinista en vez de adivinar strings libres.
INTENCIONES_VALIDAS = {
    "agendar_cita",
    "cancelar_cita",
    "reprogramar_cita",
    "consultar_disponibilidad",
    "pregunta_informacion",
    "saludo",
    "otro",
}

# Respuesta de respaldo si la API falla o el JSON viene mal formado.
# Así el resto del flujo nunca truena por un problema de red o de parsing.
FALLBACK_RESULT = {
    "intencion": "otro",
    "fecha": None,
    "hora_preferida": None,
    "nombre_cliente": None,
    "modalidad": None,
    "confianza": "baja",
}


# ---------------------------------------------------------------------------
# PROMPT DEL SISTEMA
# ---------------------------------------------------------------------------

def _build_system_prompt(today: date) -> str:
    """
    Construye el system prompt incluyendo la fecha real de hoy, para que
    Claude pueda resolver expresiones relativas ("mañana", "el viernes",
    "en 3 días") correctamente. Claude no "sabe" la fecha actual por sí
    solo, así que siempre se la inyectamos explícitamente.
    """
    dias_semana = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    hoy_texto = f"{dias_semana[today.weekday()]} {today.isoformat()}"

    return f"""Eres el motor de interpretación de un chatbot de citas por WhatsApp.
Tu ÚNICA tarea es leer el mensaje del cliente y devolver un objeto JSON
válido. NO respondas al cliente, NO agregues texto antes ni después del
JSON, NO uses backticks de markdown. Otra parte del sistema se encarga
de responderle al cliente — tú solo clasificas.

Hoy es: {hoy_texto} (zona horaria America/Mexico_City).

Devuelve exactamente estas claves:
{{
  "intencion": una de ["agendar_cita", "cancelar_cita", "reprogramar_cita", "consultar_disponibilidad", "pregunta_informacion", "saludo", "otro"],
  "fecha": fecha en formato "YYYY-MM-DD" si el cliente la mencionó o se puede
           inferir (ej. "mañana", "el viernes", "el 15 de julio"), o null si
           no se menciona ninguna fecha,
  "hora_preferida": "mañana", "tarde", "noche", una hora específica SIEMPRE
           en formato 24h "HH:MM" (convierte "1pm" -> "13:00", "9am" -> "09:00"),
           o null si no se menciona,
  "nombre_cliente": el nombre del cliente si lo menciona explícitamente,
           o null si no aparece,
  "modalidad": "virtual" ÚNICAMENTE si el paciente pide explícitamente que
           su cita sea virtual, en línea, por videollamada o a distancia.
           En cualquier otro caso: null. NUNCA asumas virtual por tu cuenta,
  "confianza": "alta", "media" o "baja", según qué tan seguro estás de
           haber entendido correctamente la intención y los datos.
}}

Reglas para interpretar la fecha (importante, sé flexible con el lenguaje
natural del cliente, incluyendo errores de tipeo o acentos faltantes):
- El cliente puede escribir con errores ("miercoles" sin acento, "d ejulio"
  con espacio de más, "agosto" mal escrito, etc.) — interpreta la intención
  real, no exijas ortografía perfecta.
- Si el cliente menciona día y mes explícitos (ej. "el 15 de julio", "15/07"),
  usa exactamente ese día y mes. Si no menciona el año, usa el año actual;
  si esa fecha ya pasó este año, usa el año siguiente.
- Si el cliente menciona un día de la semana sin fecha exacta (ej. "el
  viernes", "el próximo lunes"), calcula la fecha real más próxima a partir
  de hoy que caiga en ese día de la semana.
- Si el cliente menciona un día de la semana JUNTO con un número de día que
  no corresponde (ej. dice "miércoles 15" pero el 15 de este mes es jueves),
  prioriza el número de día explícito sobre el nombre del día de la semana
  — la gente comete ese error de dedo con frecuencia.
- Nunca inventes una fecha u hora que el cliente no mencionó ni se pueda
  inferir razonablemente del texto.

Regla de contexto (MUY importante):
- Si se te proporciona el último mensaje del asistente, interpreta la
  respuesta del paciente A LA LUZ de ese contexto. Ejemplos:
  * Asistente ofreció "¿Te gustaría agendar una cita virtual?" y el
    paciente responde "sí porfavor" -> intencion: "agendar_cita",
    modalidad: "virtual".
  * Asistente ofreció ayudar a agendar y el paciente responde "sí" ->
    intencion: "agendar_cita".
  * Asistente preguntó por el día y el paciente responde "el viernes" ->
    intencion: "agendar_cita" con la fecha resuelta.

Otras reglas importantes:
- Usa "pregunta_informacion" cuando el paciente pregunta sobre el
  consultorio o la doctora: servicios, especialidad, ubicación, horarios,
  precios, citas virtuales, o cualquier duda informativa que no sea
  agendar/cancelar/reprogramar. Ejemplos: "¿qué servicios manejan?",
  "¿dónde están ubicados?", "¿me das información de la doctora?",
  "¿atienden en línea?".
- Si el mensaje es ambiguo o no tiene relación con agendar citas, usa
  intencion: "otro" y confianza: "baja".
- Si el cliente solo saluda ("hola", "buenas tardes"), usa intencion: "saludo".
- Responde SOLO con el JSON. Nada de texto antes o después."""


# ---------------------------------------------------------------------------
# FUNCIÓN PRINCIPAL
# ---------------------------------------------------------------------------

def extract_intent(user_message: str, today: date | None = None,
                   contexto_previo: str | None = None) -> dict:
    """
    Envía el mensaje del cliente a Claude Haiku 4.5 y devuelve un dict
    con la intención y los datos extraídos.

    Args:
        user_message: texto crudo que el cliente escribió por WhatsApp.
        today: fecha de referencia para resolver expresiones relativas.
               Si no se especifica, se usa la fecha actual en
               America/Mexico_City. Parámetro útil también para pruebas.
        contexto_previo: el último mensaje que el BOT le envió al
               paciente. Fundamental para interpretar respuestas cortas
               como "sí porfavor" — sin este contexto, un "sí" aislado
               no significa nada y caería en intención "otro".

    Returns:
        dict con las claves: intencion, fecha, hora_preferida,
        nombre_cliente, confianza. Si algo falla (red, JSON inválido),
        devuelve FALLBACK_RESULT en vez de lanzar una excepción, para que
        el webhook nunca se caiga por un problema de esta capa.
    """
    if not user_message or not user_message.strip():
        return dict(FALLBACK_RESULT)

    if today is None:
        today = datetime.now(TIMEZONE).date()

    system_prompt = _build_system_prompt(today)

    # Si tenemos el último mensaje del bot, lo incluimos como contexto
    # para que respuestas cortas ("sí", "esa está bien", "porfa") se
    # interpreten correctamente. Se recorta para controlar el costo.
    if contexto_previo:
        contenido = (
            f"Último mensaje que el asistente le envió al paciente:\n"
            f"«{contexto_previo[:500]}»\n\n"
            f"Respuesta del paciente:\n«{user_message}»"
        )
    else:
        contenido = user_message

    try:
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": contenido}],
        )
    except anthropic.APIError as exc:
        logger.error("Error de la API de Claude al interpretar el mensaje: %s", exc)
        return dict(FALLBACK_RESULT)
    except Exception as exc:
        logger.error("Error inesperado al llamar a la API de Claude: %s", exc)
        return dict(FALLBACK_RESULT)

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()

    # Por si el modelo llegara a envolver el JSON en backticks de markdown
    # (no debería, pero es una salvaguarda barata).
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:].strip()

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        # El modelo a veces agrega texto después del JSON. Intentamos
        # rescatar el PRIMER objeto JSON del texto con raw_decode, que
        # ignora lo que venga después.
        try:
            inicio = raw_text.index("{")
            result, _ = json.JSONDecoder().raw_decode(raw_text[inicio:])
            logger.info("JSON rescatado de una respuesta con texto extra.")
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("Claude devolvió JSON irrecuperable ('%s'): %s", raw_text[:200], exc)
            return dict(FALLBACK_RESULT)

    # Validación defensiva: nos aseguramos de que la intención esté dentro
    # del set permitido y de que no falten claves, sin confiar ciegamente
    # en el output del modelo.
    if result.get("intencion") not in INTENCIONES_VALIDAS:
        logger.warning("Intención fuera de catálogo: %s", result.get("intencion"))
        result["intencion"] = "otro"
        result["confianza"] = "baja"

    for key in FALLBACK_RESULT:
        result.setdefault(key, FALLBACK_RESULT[key])

    return result


# ---------------------------------------------------------------------------
# RESPUESTA A PREGUNTAS INFORMATIVAS
# ---------------------------------------------------------------------------

_FALLBACK_ANSWER = (
    "Esa información no la tengo a la mano en este momento. 🙏 Puedes "
    "preguntar directamente al consultorio o, si gustas, te ayudo a "
    "agendar una cita para resolver todas tus dudas con la doctora."
)


def answer_question(user_message: str) -> str:
    """
    Responde una pregunta informativa del paciente (servicios, ubicación,
    horarios, información de la doctora, citas virtuales, etc.) usando
    únicamente el contexto del consultorio definido en business_config.

    Reglas duras que se le imponen al modelo:
      - Solo información que esté en el contexto; si no la tiene, lo dice
        e invita a preguntar directamente o a agendar.
      - JAMÁS da consejo médico, diagnósticos ni opiniones clínicas.
      - Respuestas breves, cálidas y en el tono del asistente.

    Si la API falla, devuelve un mensaje de respaldo amable en vez de
    lanzar una excepción.
    """
    if not user_message or not user_message.strip():
        return _FALLBACK_ANSWER

    system_prompt = f"""{business_config.TONE_INSTRUCTIONS}

Tu tarea AHORA: responder la pregunta del paciente de forma breve
(2-5 oraciones máximo), cálida y útil, usando ÚNICAMENTE la información
del contexto del consultorio que aparece arriba.

Reglas estrictas:
- Si la respuesta NO está en el contexto (por ejemplo, precios que no
  aparecen), dilo honestamente e invita a preguntar directamente al
  consultorio o a agendar una cita. NUNCA inventes datos.
- JAMÁS des consejo médico, diagnósticos ni opiniones sobre síntomas.
  Si la pregunta es clínica, responde con empatía y sugiere agendar una
  consulta para una valoración adecuada.
- Si mencionas la ubicación, incluye este enlace de Google Maps:
  {business_config.CONSULTORIO_MAPS_URL}
- Cierra ofreciendo ayuda para agendar solo cuando sea natural, sin ser
  insistente.
- Responde SOLO con el texto del mensaje para el paciente. Sin JSON,
  sin comillas, sin encabezados."""

    try:
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        logger.error("Error de la API de Claude al responder pregunta: %s", exc)
        return _FALLBACK_ANSWER
    except Exception as exc:
        logger.error("Error inesperado al responder pregunta: %s", exc)
        return _FALLBACK_ANSWER

    texto = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()

    return texto or _FALLBACK_ANSWER


# ---------------------------------------------------------------------------
# PRUEBA RÁPIDA MANUAL (puedes borrar este bloque en producción)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    ejemplos = [
        "hola, tienen algo disponible mañana en la tarde?",
        "quiero cancelar mi cita del viernes",
        "buenas!",
        "necesito reagendar, soy Carlos Mendoza",
    ]

    for mensaje in ejemplos:
        resultado = extract_intent(mensaje)
        print(f"'{mensaje}' -> {resultado}")
