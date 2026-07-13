"""
conversation_manager.py
------------------------
Máquina de estados simple, por número de teléfono, que orquesta el resto
de los módulos:

  nlu_service.py      -> entiende qué quiere el cliente
  rules_engine.py      -> calcula horarios válidos
  calendar_service.py  -> lee/escribe en Google Calendar
  whatsapp_client.py   -> construye los textos de respuesta

main.py solo llama a handle_incoming_message() y manda la respuesta.
Toda la lógica de "en qué paso de la conversación va cada cliente" vive
aquí.

Nota sobre persistencia: para mantenerlo simple, el estado se guarda en
un diccionario en memoria. Esto significa que si reinicias el proceso
se pierden las conversaciones a medias (no las citas ya creadas, esas
están seguras en Google Calendar). Para producción real, considera
mover CONVERSATIONS a Redis o SQLite.
"""

import logging
from datetime import date, datetime

import calendar_service
import rules_engine
import nlu_service
import whatsapp_client

logger = logging.getLogger("conversation_manager")

# ---------------------------------------------------------------------------
# ESTADO EN MEMORIA
# ---------------------------------------------------------------------------

# Estructura por número de teléfono:
# {
#   "stage": "inicio" | "esperando_seleccion_horario",
#   "fecha": date | None,
#   "slots_ofrecidos": list[str],
#   "nombre_cliente": str | None,
# }
CONVERSATIONS: dict[str, dict] = {}

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _get_state(phone_number: str) -> dict:
    if phone_number not in CONVERSATIONS:
        CONVERSATIONS[phone_number] = {
            "stage": "inicio",
            "fecha": None,
            "slots_ofrecidos": [],
            "nombre_cliente": None,
        }
    return CONVERSATIONS[phone_number]


def _reset_state(phone_number: str) -> None:
    CONVERSATIONS[phone_number] = {
        "stage": "inicio",
        "fecha": None,
        "slots_ofrecidos": [],
        "nombre_cliente": None,
    }


def _format_fecha_legible(fecha: date) -> str:
    """Convierte un date a texto amigable: 'viernes 18 de julio'."""
    return f"{DIAS_SEMANA[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]}"


# ---------------------------------------------------------------------------
# INTERPRETACIÓN DE LA SELECCIÓN DE HORARIO
# ---------------------------------------------------------------------------

def _match_slot_selection(message: str, slots_ofrecidos: list[str]) -> str | None:
    """
    Cuando ya le mostramos al cliente una lista numerada de horarios,
    intentamos hacer match con su respuesta sin volver a llamar a Claude
    (ahorra costo y latencia para el caso más común).

    Acepta: "1", "la 1", "la primera", o la hora directa "10:15".
    Devuelve la hora "HH:MM" seleccionada, o None si no hubo match claro.
    """
    texto = message.strip().lower()

    # Coincidencia directa por número de la lista (1-indexado).
    for i, hora in enumerate(slots_ofrecidos):
        numero = str(i + 1)
        if texto == numero or f"la {numero}" in texto or f"opción {numero}" in texto or f"opcion {numero}" in texto:
            return hora

    # Coincidencia directa por hora exacta mencionada en el texto.
    for hora in slots_ofrecidos:
        if hora in texto:
            return hora

    return None


# ---------------------------------------------------------------------------
# ORQUESTADOR PRINCIPAL
# ---------------------------------------------------------------------------

def handle_incoming_message(phone_number: str, message_body: str, profile_name: str | None = None) -> str:
    """
    Punto de entrada único que main.py llama por cada mensaje entrante.

    Returns:
        El texto de respuesta que debe enviarse al cliente por WhatsApp.
        Esta función nunca lanza excepciones hacia afuera: cualquier
        error interno se traduce en un mensaje amigable de error.
    """
    state = _get_state(phone_number)

    try:
        # -------------------------------------------------------------
        # PASO 1: si ya le mostramos horarios, primero intentamos hacer
        # match directo con su respuesta antes de gastar una llamada a
        # Claude (la mayoría de las respuestas aquí son "2" o "10:15").
        # -------------------------------------------------------------
        if state["stage"] == "esperando_seleccion_horario":
            hora_elegida = _match_slot_selection(message_body, state["slots_ofrecidos"])

            if hora_elegida:
                nombre = state["nombre_cliente"] or profile_name
                evento = calendar_service.create_appointment(
                    target_date=state["fecha"],
                    start_time_str=hora_elegida,
                    client_name=nombre or "Cliente WhatsApp",
                    phone_number=phone_number,
                )
                fecha_legible = _format_fecha_legible(state["fecha"])
                _reset_state(phone_number)
                logger.info("Cita creada para %s: %s", phone_number, evento.get("htmlLink"))
                return whatsapp_client.build_confirmation_message(fecha_legible, hora_elegida, nombre)

            # No hubo match: seguimos en el mismo estado y le pedimos
            # que aclare, sin gastar una llamada a Claude para esto.
            return (
                "No logré identificar el horario. ¿Podrías decirme el número "
                "de la lista o la hora exacta? (ej. \"2\" o \"10:15\")"
            )

        # -------------------------------------------------------------
        # PASO 2: conversación nueva o el cliente cambió de tema.
        # Usamos Claude (Haiku) para entender la intención.
        # -------------------------------------------------------------
        resultado_nlu = nlu_service.extract_intent(message_body)
        intencion = resultado_nlu["intencion"]

        if resultado_nlu.get("nombre_cliente"):
            state["nombre_cliente"] = resultado_nlu["nombre_cliente"]

        nombre_para_saludo = state["nombre_cliente"] or profile_name

        if intencion == "saludo":
            return whatsapp_client.build_greeting_message(nombre_para_saludo)

        if intencion in ("agendar_cita", "consultar_disponibilidad"):
            fecha_str = resultado_nlu.get("fecha")

            if not fecha_str:
                return (
                    "Claro, ¿para qué día te gustaría la cita? "
                    "(puedes decir \"mañana\", \"el viernes\", o una fecha exacta)"
                )

            try:
                fecha = date.fromisoformat(fecha_str)
            except ValueError:
                logger.warning("Fecha inválida devuelta por NLU: %s", fecha_str)
                return "No logré entender bien la fecha, ¿podrías decírmela de otra forma?"

            try:
                eventos_ocupados = calendar_service.get_busy_events(fecha)
            except Exception as exc:
                logger.error("Fallo al consultar Google Calendar: %s", exc)
                return whatsapp_client.build_error_message()

            slots = rules_engine.generate_available_slots(fecha, eventos_ocupados)
            fecha_legible = _format_fecha_legible(fecha)

            if not slots:
                return whatsapp_client.build_available_slots_message(fecha_legible, [])

            state["stage"] = "esperando_seleccion_horario"
            state["fecha"] = fecha
            state["slots_ofrecidos"] = slots
            return whatsapp_client.build_available_slots_message(fecha_legible, slots)

        if intencion in ("cancelar_cita", "reprogramar_cita"):
            # MVP: estas dos requieren poder identificar la cita existente
            # del cliente en Google Calendar (por número de teléfono o
            # correo), lo cual queda como siguiente iteración del proyecto.
            return (
                "Por ahora no puedo cancelar o reprogramar citas de forma "
                "automática. Escríbenos directamente y con gusto te ayudamos. 🙏"
            )

        # intencion == "otro"
        return (
            "No estoy seguro de haber entendido. Puedo ayudarte a agendar una "
            "cita — solo dime, por ejemplo, \"quiero una cita el jueves por la mañana\"."
        )

    except Exception as exc:
        logger.error("Error inesperado procesando mensaje de %s: %s", phone_number, exc)
        return whatsapp_client.build_error_message()
