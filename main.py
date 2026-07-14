"""
main.py
-------
Punto de entrada de FastAPI. Expone el webhook que Twilio invoca cada vez
que un cliente escribe por WhatsApp, y delega toda la lógica a
conversation_manager.py.

Para correrlo localmente:

    uvicorn main:app --reload --port 8000

Y expón el puerto con Ngrok para que Twilio pueda alcanzarlo:

    ngrok http 8000

Copia la URL https que te da Ngrok (ej. https://abcd1234.ngrok-free.app)
y configúrala en el Console de Twilio en:
Messaging -> Try it out -> Sandbox settings -> "WHEN A MESSAGE COMES IN"
agregando al final: /webhook/whatsapp
"""

import logging
import os

# Carga las variables de entorno desde ".env" ANTES de importar cualquier
# otro módulo del proyecto. Es importante que esto vaya primero: tanto
# whatsapp_client.py como nlu_service.py leen sus credenciales de
# os.environ en el momento en que se importan, no cuando se usan.
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Response, Header, HTTPException
from fastapi.responses import PlainTextResponse
from twilio.twiml.messaging_response import MessagingResponse
from apscheduler.schedulers.background import BackgroundScheduler

import whatsapp_client
import conversation_manager
import reminders

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")

app = FastAPI(title="WhatsApp Appointment Bot")

# -----------------------------------------------------------------------
# Si quieres activar la validación de firma de Twilio (recomendado en
# producción), pon esta variable de entorno en "true". La dejamos
# apagada por default para que el sandbox local sea más fácil de probar.
#
#     export VALIDATE_TWILIO_SIGNATURE=true
# -----------------------------------------------------------------------
VALIDATE_SIGNATURE = os.environ.get("VALIDATE_TWILIO_SIGNATURE", "false").lower() == "true"

# -----------------------------------------------------------------------
# RECORDATORIOS AUTOMÁTICOS DEL DÍA ANTERIOR
#
# Opción A (default si el bot corre 24/7, ej. Render Starter): un
# scheduler interno dispara enviar_recordatorios_de_manana() todos los
# días a la hora configurada.
#
# Opción B (recomendada si usas un plan que "duerme", ej. Render Free):
# desactiva el scheduler interno (REMINDER_SCHEDULER_ENABLED=false) y en
# su lugar configura un cron externo gratuito (cron-job.org, Render Cron
# Jobs, etc.) que le pegue una vez al día a:
#     POST /tasks/send-reminders
#     Header: X-Reminder-Secret: <tu REMINDER_SECRET>
# -----------------------------------------------------------------------
REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR", "10"))  # 10am hora de México
REMINDER_SCHEDULER_ENABLED = os.environ.get("REMINDER_SCHEDULER_ENABLED", "true").lower() == "true"
REMINDER_SECRET = os.environ.get("REMINDER_SECRET")

if REMINDER_SCHEDULER_ENABLED:
    _scheduler = BackgroundScheduler(timezone="America/Mexico_City")
    _scheduler.add_job(
        reminders.enviar_recordatorios_de_manana,
        trigger="cron",
        hour=REMINDER_HOUR,
        minute=0,
        id="recordatorios_diarios",
    )
    _scheduler.start()
    logger.info("Scheduler de recordatorios activo: todos los días a las %02d:00", REMINDER_HOUR)


@app.get("/")
def health_check():
    """Endpoint simple para confirmar que el servidor está vivo."""
    return {"status": "ok", "service": "whatsapp-appointment-bot"}


@app.post("/tasks/send-reminders")
def trigger_reminders(x_reminder_secret: str | None = Header(default=None)):
    """
    Dispara manualmente el envío de recordatorios de mañana. Pensado para
    ser llamado por un cron externo (ver nota arriba) cuando el bot corre
    en un plan que se duerme y no puede confiar en un scheduler interno.

    Protegido con un secreto simple en el header, para que no cualquiera
    en internet pueda spammear recordatorios a tus pacientes.
    """
    if REMINDER_SECRET and x_reminder_secret != REMINDER_SECRET:
        raise HTTPException(status_code=403, detail="Secreto inválido")

    resultado = reminders.enviar_recordatorios_de_manana()
    return resultado


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Recibe cada mensaje entrante de WhatsApp vía Twilio.

    Twilio manda un POST application/x-www-form-urlencoded (no JSON) con
    campos como From, Body, ProfileName, etc.
    """
    form = await request.form()
    form_dict = dict(form)

    # -------------------------------------------------------------
    # Validación opcional de que el webhook viene realmente de Twilio.
    # -------------------------------------------------------------
    if VALIDATE_SIGNATURE:
        signature = request.headers.get("X-Twilio-Signature", "")
        url_completa = str(request.url)
        if not whatsapp_client.validate_twilio_signature(url_completa, form_dict, signature):
            logger.warning("Firma de Twilio inválida, se rechaza la solicitud.")
            return Response(status_code=403)

    try:
        parsed = whatsapp_client.parse_incoming_message(form_dict)
    except Exception as exc:
        # Si el JSON/form viene con una estructura inesperada, no
        # tumbamos el servidor: solo logueamos y respondemos vacío.
        logger.error("No se pudo parsear el mensaje entrante: %s", exc)
        return Response(content=str(MessagingResponse()), media_type="application/xml")

    if not parsed["from_number"] or not parsed["body"]:
        logger.warning("Webhook recibido sin From/Body válidos: %s", form_dict)
        return Response(content=str(MessagingResponse()), media_type="application/xml")

    logger.info("Mensaje de %s: %s", parsed["from_number"], parsed["body"])

    respuesta_texto = conversation_manager.handle_incoming_message(
        phone_number=parsed["from_number"],
        message_body=parsed["body"],
        profile_name=parsed["profile_name"],
    )

    whatsapp_client.send_whatsapp_message(parsed["from_number"], respuesta_texto)

    # Respondemos con un TwiML vacío: ya enviamos la respuesta nosotros
    # mismos vía la API REST de Twilio (send_whatsapp_message), así que
    # no queremos que Twilio mande un segundo mensaje automático.
    return Response(content=str(MessagingResponse()), media_type="application/xml")
