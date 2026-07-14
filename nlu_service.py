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

    return f"""{business_config.TONE_INSTRUCTIONS}

---

Además de tu papel como asistente, tu tarea AHORA es actuar como motor
de interpretación: lee el mensaje del cliente y devuelve ÚNICAMENTE un
objeto JSON válido, sin texto adicional, sin explicaciones, sin backticks
de markdown.

Hoy es: {hoy_texto} (zona horaria America/Mexico_City).

Devuelve exactamente estas claves:
{{
  "intencion": una de ["agendar_cita", "cancelar_cita", "reprogramar_cita", "consultar_disponibilidad", "saludo", "otro"],
  "fecha": fecha en formato "YYYY-MM-DD" si el cliente la mencionó o se puede
           inferir (ej. "mañana", "el viernes", "el 15 de julio"), o null si
           no se menciona ninguna fecha,
  "hora_preferida": "mañana", "tarde", "noche", una hora específica SIEMPRE
           en formato 24h "HH:MM" (convierte "1pm" -> "13:00", "9am" -> "09:00"),
           o null si no se menciona,
  "nombre_cliente": el nombre del cliente si lo menciona explícitamente,
           o null si no aparece,
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

Otras reglas importantes:
- Si el mensaje es ambiguo o no tiene relación con agendar citas, usa
  intencion: "otro" y confianza: "baja".
- Si el cliente solo saluda ("hola", "buenas tardes"), usa intencion: "saludo".
- Responde SOLO con el JSON. Nada de texto antes o después."""


# ---------------------------------------------------------------------------
# FUNCIÓN PRINCIPAL
# ---------------------------------------------------------------------------

def extract_intent(user_message: str, today: date | None = None) -> dict:
    """
    Envía el mensaje del cliente a Claude Haiku 4.5 y devuelve un dict
    con la intención y los datos extraídos.

    Args:
        user_message: texto crudo que el cliente escribió por WhatsApp.
        today: fecha de referencia para resolver expresiones relativas.
               Si no se especifica, se usa la fecha actual en
               America/Mexico_City. Parámetro útil también para pruebas.

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

    try:
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
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
    except json.JSONDecodeError as exc:
        logger.warning("Claude devolvió JSON inválido ('%s'): %s", raw_text, exc)
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
