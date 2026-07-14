"""
whatsapp_client.py
-------------------
Módulo para enviar y recibir mensajes de WhatsApp a través de Twilio.

Se eligió Twilio (en vez de conectarse directo a la Meta Cloud API) porque:
  - Sigue siendo un canal 100% oficial (Twilio es Business Solution
    Provider verificado por Meta), no hay ningún riesgo de baneo.
  - El sandbox de pruebas se activa en minutos, sin verificación de
    negocio ni tokens que expiran cada 24h.
  - El SDK de Python es mucho más simple que armar a mano los payloads
    JSON de la Cloud API.

Si en el futuro migras a la Meta Cloud API directa, solo se reescribe
este archivo — rules_engine.py, calendar_service.py y nlu_service.py
no cambian en absoluto.
"""

import os
import logging

from twilio.rest import Client
from twilio.request_validator import RequestValidator
from twilio.base.exceptions import TwilioRestException

import business_config

logger = logging.getLogger("whatsapp_client")

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

# -----------------------------------------------------------------------
# >>> AQUÍ VAN TUS CREDENCIALES DE TWILIO <<<
#
# 1. Crea una cuenta en https://www.twilio.com/try-twilio
# 2. En el Console Dashboard copia tu "Account SID" y tu "Auth Token".
# 3. Activa el sandbox de WhatsApp en Messaging -> Try it out ->
#    Send a WhatsApp message. Ahí te dan un número (siempre empieza con
#    whatsapp:+14155238886 en el sandbox) y un código para unirte
#    ("join palabra-clave") que debes mandar desde tu celular primero.
# 4. Configura estas variables de entorno antes de correr el bot:
#
#       export TWILIO_ACCOUNT_SID="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#       export TWILIO_AUTH_TOKEN="tu_auth_token"
#       export TWILIO_WHATSAPP_NUMBER="whatsapp:+14155238886"
#
#    En producción, TWILIO_WHATSAPP_NUMBER será tu número de WhatsApp
#    Business real una vez que Twilio lo verifique.
# -----------------------------------------------------------------------
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER")

_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


# ---------------------------------------------------------------------------
# ENVÍO DE MENSAJES
# ---------------------------------------------------------------------------

def send_whatsapp_message(to_number: str, body: str) -> bool:
    """
    Envía un mensaje de texto plano por WhatsApp usando Twilio.

    Args:
        to_number: número del cliente. Acepta tanto "+521234567890" como
                   "whatsapp:+521234567890" (se normaliza automáticamente).
        body: texto del mensaje a enviar.

    Returns:
        True si el mensaje se envió correctamente, False si Twilio lo
        rechazó (número inválido, fuera del sandbox, etc.). Nunca lanza
        una excepción hacia afuera para no tumbar el webhook por un
        problema de envío.
    """
    if not to_number.startswith("whatsapp:"):
        to_number = f"whatsapp:{to_number}"

    try:
        message = _client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number,
            body=body,
        )
        logger.info("Mensaje enviado a %s (sid=%s)", to_number, message.sid)
        return True
    except TwilioRestException as exc:
        logger.error("Twilio rechazó el mensaje a %s: %s", to_number, exc)
        return False
    except Exception as exc:
        logger.error("Error inesperado al enviar mensaje de WhatsApp: %s", exc)
        return False


# ---------------------------------------------------------------------------
# CONSTRUCCIÓN DE MENSAJES (texto plano, legible y profesional)
# ---------------------------------------------------------------------------
#
# Nota de diseño: el sandbox de Twilio no soporta listas/botones interactivos
# de WhatsApp sin registrar plantillas de "Content API" previamente
# (trámite adicional con Meta). Para mantener el setup simple, usamos
# texto plano bien formateado + la capa de NLU (nlu_service.py) para
# interpretar la respuesta del cliente en lenguaje natural, ej.
# "la de las 10:15" o "la segunda". Si más adelante registras plantillas
# de Content API, estas funciones se pueden reemplazar por mensajes
# interactivos reales sin tocar el resto del sistema.

def build_greeting_message(nombre_cliente: str | None = None) -> str:
    """Mensaje de bienvenida cuando el cliente solo saluda."""
    return business_config.saludo_bienvenida(nombre_cliente)


def build_available_slots_message(fecha_legible: str, slots: list[str]) -> str:
    """
    Construye el mensaje con los horarios disponibles para una fecha.

    Args:
        fecha_legible: fecha en formato amigable, ej. "viernes 18 de julio".
        slots: lista de horas "HH:MM" devuelta por rules_engine.generate_available_slots.
    """
    if not slots:
        return (
            f"Lo siento, no tengo horarios disponibles para {fecha_legible}. 😕\n"
            "¿Quieres que busquemos otro día?"
        )

    lineas = [f"  {i + 1}. {hora}" for i, hora in enumerate(slots)]
    horarios_texto = "\n".join(lineas)

    return (
        f"Estos son los horarios disponibles para {fecha_legible}:\n\n"
        f"{horarios_texto}\n\n"
        "Solo dime la hora que prefieras (ej.\"10:15\")."
    )


def build_confirmation_message(fecha_legible: str, hora: str, nombre_cliente: str | None = None) -> str:
    """Mensaje de confirmación tras crear la cita en Google Calendar."""
    nombre_texto = f"{nombre_cliente}, tu" if nombre_cliente else "Tu"
    return (
        f"✅ ¡Listo! {nombre_texto} cita quedó confirmada:\n\n"
        f"📅 {fecha_legible}\n"
        f"🕐 {hora}\n\n"
        f"{business_config.despedida_confirmacion()}"
    )


def build_no_appointments_message() -> str:
    """Cuando el cliente pide cancelar/reprogramar pero no tiene citas registradas."""
    return (
        "No encontré ninguna cita activa a tu nombre con este número. 🤔\n"
        "Te recordamos que solo podemos ver las citas agendadas con este número de WhatsApp. \n"
        "Si crees que es un error, contáctanos directamente."
    )


def build_appointment_selection_message(citas_legibles: list[str]) -> str:
    """
    Lista numerada de citas cuando el cliente tiene más de una y hay que
    preguntarle cuál quiere cancelar o reprogramar.

    Args:
        citas_legibles: lista de strings ya formateados, ej.
                         ["viernes 18 de julio a las 10:15", ...]
    """
    lineas = [f"  {i + 1}. {cita}" for i, cita in enumerate(citas_legibles)]
    return "Tienes varias citas registradas:\n\n" + "\n".join(lineas) + "\n\n¿Cuál te refieres? (dime el número)"


def build_cancel_confirm_prompt(cita_legible: str) -> str:
    """Pide confirmación antes de cancelar definitivamente una cita."""
    return f"Tienes una cita el {cita_legible}. ¿Confirmas que quieres cancelarla? (sí/no)"


def build_cancel_success_message() -> str:
    return "✅ Tu cita fue cancelada correctamente. Si quieres agendar otra, mandame mensaje."


def build_cancel_aborted_message() -> str:
    return "Entendido, tu cita se mantiene sin cambios. 🙂"


def build_reschedule_prompt(cita_legible: str) -> str:
    """Pide la nueva fecha/hora una vez identificada la cita a reprogramar."""
    return (
        f"Tu cita actual es el {cita_legible}. "
        "¿Para qué nuevo día y hora te gustaría moverla?"
    )


def build_ask_confirm_appointment_message(fecha_legible: str, hora: str) -> str:
    """
    Confirmación intermedia: después de que el paciente elige fecha y
    hora, le pedimos que confirme antes de pasar a nombre/motivo. Así
    evitamos que un tap accidental o una mala elección llegue hasta el
    final del flujo.
    """
    return (
        f"Perfecto, la cita quedaría el {fecha_legible} a las {hora}. 📅\n\n"
        "¿Es correcto? (responde \"sí\" o \"no\")"
    )


def build_ask_name_message(fecha_legible: str, hora: str) -> str:
    """Después de que el cliente elige un horario, le pedimos su nombre."""
    return (
        f"Perfecto, {fecha_legible} a las {hora}. 📌\n\n"
        "¿A nombre de quién agendo la cita?"
    )


def build_ask_description_message() -> str:
    """Después del nombre, le pedimos una breve descripción de la cita."""
    return (
        "¡Gracias! Una última cosa: ¿me podrías dar una breve descripción "
        "del motivo de la cita? (ej. \"consulta general\", \"revisión de resultados\")"
    )


def build_error_message() -> str:
    """Mensaje genérico y amable cuando algo falla internamente."""
    return (
        "Ups, tuve un problema para procesar tu solicitud. 🙏\n"
        "¿Podrías intentarlo de nuevo en un momento?"
    )


# ---------------------------------------------------------------------------
# RECEPCIÓN DE MENSAJES (webhook entrante de Twilio)
# ---------------------------------------------------------------------------

def parse_incoming_message(form_data: dict) -> dict:
    """
    Extrae los datos relevantes del webhook que Twilio manda a tu servidor
    (POST application/x-www-form-urlencoded) cuando un cliente escribe.

    Args:
        form_data: diccionario con los campos del POST de Twilio
                   (en FastAPI: dict(await request.form())).

    Returns:
        {
            "from_number": "+521234567890",   # sin el prefijo "whatsapp:"
            "body": "texto del mensaje",
            "profile_name": "Nombre en WhatsApp" | None,
        }
    """
    raw_from = form_data.get("From", "")
    from_number = raw_from.replace("whatsapp:", "").strip()

    return {
        "from_number": from_number,
        "body": form_data.get("Body", "").strip(),
        "profile_name": form_data.get("ProfileName") or None,
    }


def validate_twilio_signature(url: str, form_data: dict, signature_header: str) -> bool:
    """
    Verifica que el webhook realmente venga de Twilio y no de un tercero
    que le pegó a tu URL pública directamente.

    Úsala en main.py así:
        signature = request.headers.get("X-Twilio-Signature", "")
        if not validate_twilio_signature(str(request.url), form_dict, signature):
            raise HTTPException(status_code=403, detail="Firma inválida")

    Args:
        url: URL completa que Twilio invocó (debe coincidir exactamente
             con la configurada en el Console de Twilio, incluyendo https).
        form_data: mismos datos crudos del POST.
        signature_header: valor del header "X-Twilio-Signature".

    Returns:
        True si la firma es válida.
    """
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    return validator.validate(url, form_data, signature_header)


# ---------------------------------------------------------------------------
# PRUEBA RÁPIDA MANUAL (puedes borrar este bloque en producción)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Reemplaza por tu número (el que se unió al sandbox) para probar.
    numero_prueba = "+521234567890"

    mensaje = build_available_slots_message("viernes 18 de julio", ["09:00", "10:15", "11:30"])
    print(mensaje)
    print("\n--- Enviando mensaje de prueba ---")
    exito = send_whatsapp_message(numero_prueba, mensaje)
    print(f"Envío exitoso: {exito}")
