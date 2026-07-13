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
#   "stage": uno de:
#       "inicio"
#       "esperando_seleccion_horario"              (agendando cita nueva)
#       "esperando_seleccion_cita"                  (cancelar/reprogramar: elegir cuál)
#       "esperando_confirmacion_cancelacion"        (cancelar: confirmar sí/no)
#       "esperando_nueva_fecha_reprogramacion"      (reprogramar: pedir nueva fecha)
#       "esperando_seleccion_horario_reprogramacion" (reprogramar: elegir nuevo horario)
#   "fecha": date | None,
#   "slots_ofrecidos": list[str],
#   "nombre_cliente": str | None,
#   "citas_encontradas": list[dict],   # candidatas al buscar por teléfono
#   "evento_objetivo": dict | None,    # la cita puntual sobre la que se actúa
# }
CONVERSATIONS: dict[str, dict] = {}

AFIRMACIONES = {"si", "sí", "sí.", "confirmo", "correcto", "va", "dale", "yes", "ok", "okay"}
NEGACIONES = {"no", "no.", "cancela eso", "mejor no", "nel"}

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _estado_vacio() -> dict:
    return {
        "stage": "inicio",
        "fecha": None,
        "slots_ofrecidos": [],
        "nombre_cliente": None,
        "citas_encontradas": [],
        "evento_objetivo": None,
        "accion_pendiente": None,  # "cancelar" | "reprogramar", mientras se elige la cita
    }


def _get_state(phone_number: str) -> dict:
    if phone_number not in CONVERSATIONS:
        CONVERSATIONS[phone_number] = _estado_vacio()
    return CONVERSATIONS[phone_number]


def _reset_state(phone_number: str) -> None:
    # Conservamos el nombre del cliente entre conversaciones para no
    # tener que volver a pedirlo cada vez.
    nombre_previo = CONVERSATIONS.get(phone_number, {}).get("nombre_cliente")
    CONVERSATIONS[phone_number] = _estado_vacio()
    CONVERSATIONS[phone_number]["nombre_cliente"] = nombre_previo


def _format_fecha_hora_legible(dt: datetime) -> str:
    """Convierte un datetime a texto amigable: 'viernes 18 de julio a las 10:15'."""
    hora = dt.strftime("%H:%M")
    return f"{_format_fecha_legible(dt.date())} a las {hora}"


def _parse_si_no(message: str) -> bool | None:
    """
    Interpreta una respuesta de confirmación sin gastar una llamada a
    Claude (el caso más común aquí es un simple "sí" o "no").

    Returns:
        True si es afirmación, False si es negación, None si no está claro.
    """
    texto = message.strip().lower()
    if texto in AFIRMACIONES or texto.startswith("si "):
        return True
    if texto in NEGACIONES or texto.startswith("no "):
        return False
    return None


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


def _match_appointment_selection(message: str, citas: list[dict]) -> dict | None:
    """
    Igual que _match_slot_selection pero para elegir entre una lista de
    citas existentes (usado en cancelación/reprogramación cuando el
    cliente tiene más de una cita activa).
    """
    texto = message.strip().lower()

    for i, cita in enumerate(citas):
        numero = str(i + 1)
        if texto == numero or f"la {numero}" in texto or f"opción {numero}" in texto or f"opcion {numero}" in texto:
            return cita

    return None


# ---------------------------------------------------------------------------
# FLUJO DE CANCELACIÓN / REPROGRAMACIÓN
# ---------------------------------------------------------------------------

def _iniciar_busqueda_de_cita(phone_number: str, state: dict, accion: str) -> str:
    """
    Primer paso común a "cancelar" y "reprogramar": buscar las citas
    futuras del cliente y decidir el siguiente paso según cuántas
    encuentre.

    Args:
        accion: "cancelar" o "reprogramar" — determina a qué estado se
                pasa una vez identificada la cita concreta.
    """
    try:
        citas = calendar_service.find_appointments_by_phone(phone_number)
    except Exception as exc:
        logger.error("Fallo al buscar citas de %s: %s", phone_number, exc)
        return whatsapp_client.build_error_message()

    if not citas:
        _reset_state(phone_number)
        return whatsapp_client.build_no_appointments_message()

    if len(citas) == 1:
        return _avanzar_con_cita_elegida(state, citas[0], accion)

    # Varias citas: le pedimos al cliente que elija cuál.
    state["stage"] = "esperando_seleccion_cita"
    state["citas_encontradas"] = citas
    state["accion_pendiente"] = accion
    citas_legibles = [_format_fecha_hora_legible(c["start"]) for c in citas]
    return whatsapp_client.build_appointment_selection_message(citas_legibles)


def _avanzar_con_cita_elegida(state: dict, cita: dict, accion: str) -> str:
    """Una vez identificada una única cita concreta, decide el siguiente paso."""
    state["evento_objetivo"] = cita
    cita_legible = _format_fecha_hora_legible(cita["start"])

    if accion == "cancelar":
        state["stage"] = "esperando_confirmacion_cancelacion"
        return whatsapp_client.build_cancel_confirm_prompt(cita_legible)

    # accion == "reprogramar"
    state["stage"] = "esperando_nueva_fecha_reprogramacion"
    return whatsapp_client.build_reschedule_prompt(cita_legible)


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
    stage = state["stage"]

    try:
        # -------------------------------------------------------------
        # Estados que esperan una respuesta puntual del cliente se
        # resuelven aquí, SIN llamar a Claude — son matches deterministas
        # (número de lista, hora exacta, sí/no) y así ahorramos costo y
        # latencia en el caso más común.
        # -------------------------------------------------------------

        if stage == "esperando_seleccion_horario":
            hora_elegida = _match_slot_selection(message_body, state["slots_ofrecidos"])
            if not hora_elegida:
                return (
                    "No logré identificar el horario. ¿Podrías decirme el número "
                    "de la lista o la hora exacta? (ej. \"2\" o \"10:15\")"
                )

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

        if stage == "esperando_seleccion_cita":
            cita_elegida = _match_appointment_selection(message_body, state["citas_encontradas"])
            if not cita_elegida:
                return "No logré identificar cuál cita. ¿Podrías decirme el número de la lista?"
            return _avanzar_con_cita_elegida(state, cita_elegida, state["accion_pendiente"])

        if stage == "esperando_confirmacion_cancelacion":
            confirmacion = _parse_si_no(message_body)
            if confirmacion is None:
                return "¿Confirmas que quieres cancelar esa cita? Responde \"sí\" o \"no\"."

            evento = state["evento_objetivo"]
            if confirmacion is False:
                _reset_state(phone_number)
                return whatsapp_client.build_cancel_aborted_message()

            try:
                calendar_service.delete_appointment(evento["id"])
            except Exception as exc:
                logger.error("Fallo al cancelar la cita %s: %s", evento.get("id"), exc)
                return whatsapp_client.build_error_message()

            _reset_state(phone_number)
            return whatsapp_client.build_cancel_success_message()

        if stage == "esperando_nueva_fecha_reprogramacion":
            # Aquí sí usamos Claude, porque la nueva fecha viene en
            # lenguaje libre ("el próximo lunes en la mañana", etc.).
            resultado_nlu = nlu_service.extract_intent(message_body)
            fecha_str = resultado_nlu.get("fecha")

            if not fecha_str:
                return "¿Para qué día te gustaría moverla? (ej. \"el lunes\", \"mañana\")"

            try:
                nueva_fecha = date.fromisoformat(fecha_str)
            except ValueError:
                logger.warning("Fecha inválida devuelta por NLU al reprogramar: %s", fecha_str)
                return "No logré entender bien la fecha, ¿podrías decírmela de otra forma?"

            evento_actual = state["evento_objetivo"]
            try:
                eventos_ocupados = calendar_service.get_busy_events(nueva_fecha)
            except Exception as exc:
                logger.error("Fallo al consultar Google Calendar: %s", exc)
                return whatsapp_client.build_error_message()

            # Excluimos la propia cita que se está moviendo de los
            # eventos ocupados: si no, se bloquearía a sí misma cuando
            # el cliente reprograma dentro del mismo día.
            eventos_ocupados = [e for e in eventos_ocupados if e.get("id") != evento_actual.get("id")]

            slots = rules_engine.generate_available_slots(nueva_fecha, eventos_ocupados)
            fecha_legible = _format_fecha_legible(nueva_fecha)

            if not slots:
                return whatsapp_client.build_available_slots_message(fecha_legible, [])

            state["stage"] = "esperando_seleccion_horario_reprogramacion"
            state["fecha"] = nueva_fecha
            state["slots_ofrecidos"] = slots
            return whatsapp_client.build_available_slots_message(fecha_legible, slots)

        if stage == "esperando_seleccion_horario_reprogramacion":
            hora_elegida = _match_slot_selection(message_body, state["slots_ofrecidos"])
            if not hora_elegida:
                return (
                    "No logré identificar el horario. ¿Podrías decirme el número "
                    "de la lista o la hora exacta? (ej. \"2\" o \"10:15\")"
                )

            evento_actual = state["evento_objetivo"]
            try:
                calendar_service.update_appointment(
                    event_id=evento_actual["id"],
                    new_date=state["fecha"],
                    new_start_time_str=hora_elegida,
                )
            except Exception as exc:
                logger.error("Fallo al reprogramar la cita %s: %s", evento_actual.get("id"), exc)
                return whatsapp_client.build_error_message()

            nombre = state["nombre_cliente"] or profile_name
            fecha_legible = _format_fecha_legible(state["fecha"])
            _reset_state(phone_number)
            return whatsapp_client.build_confirmation_message(fecha_legible, hora_elegida, nombre)

        # -------------------------------------------------------------
        # Conversación nueva (stage == "inicio") o el cliente escribió
        # algo fuera de flujo. Usamos Claude (Haiku) para entender la
        # intención.
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

        if intencion == "cancelar_cita":
            return _iniciar_busqueda_de_cita(phone_number, state, accion="cancelar")

        if intencion == "reprogramar_cita":
            return _iniciar_busqueda_de_cita(phone_number, state, accion="reprogramar")

        # intencion == "otro"
        return (
            "No estoy seguro de haber entendido. Puedo ayudarte a agendar, cancelar "
            "o reprogramar una cita — solo dime, por ejemplo, \"quiero una cita el "
            "jueves por la mañana\"."
        )

    except Exception as exc:
        logger.error("Error inesperado procesando mensaje de %s: %s", phone_number, exc)
        return whatsapp_client.build_error_message()
