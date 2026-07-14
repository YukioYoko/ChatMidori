"""
calendar_service.py
--------------------
Módulo exclusivo para interactuar con la API de Google Calendar.

Responsabilidades:
  - Autenticación OAuth2 (credentials.json / token.json).
  - Lectura de eventos ocupados de un día específico.
  - Creación de nuevas citas cuando el cliente confirma un horario.

Este módulo NO conoce las reglas de negocio (eso vive en rules_engine.py).
Solo habla con Google y devuelve datos "crudos" ya convertidos a datetime.
"""

import os
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger("calendar_service")

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

# Alcance de permisos: lectura y escritura de eventos en el calendario.
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# ID del calendario a usar. "primary" apunta al calendario principal de la
# cuenta autenticada. Si usas un calendario secundario, reemplázalo aquí
# por su ID (lo encuentras en la configuración del calendario en Google).
CALENDAR_ID = "primary"

# Debe coincidir con la zona horaria usada en rules_engine.py
TIMEZONE = ZoneInfo("America/Mexico_City")
TIMEZONE_NAME = "America/Mexico_City"

# -----------------------------------------------------------------------
# >>> AQUÍ VAN TUS CREDENCIALES DE GOOGLE <<<
#
# 1. Ve a Google Cloud Console -> APIs & Services -> Credentials.
# 2. Crea unas credenciales OAuth 2.0 de tipo "Desktop App".
# 3. Descarga el archivo JSON y guárdalo en la raíz del proyecto como:
#       credentials.json
# 4. La primera vez que corras el bot, se abrirá una ventana del navegador
#    para que autorices el acceso. Esto generará automáticamente:
#       token.json
#    (contiene el access_token/refresh_token; NO lo subas a git).
# -----------------------------------------------------------------------
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


# ---------------------------------------------------------------------------
# AUTENTICACIÓN
# ---------------------------------------------------------------------------

def _get_credentials() -> Credentials:
    """
    Carga credenciales válidas desde token.json, refrescándolas si expiraron,
    o disparando el flujo OAuth interactivo si es la primera vez.
    """
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                logger.warning("No se pudo refrescar el token, se re-autenticará: %s", exc)
                creds = None

        if not creds:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"No se encontró '{CREDENTIALS_FILE}'. Descárgalo desde "
                    "Google Cloud Console y colócalo en la raíz del proyecto."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # Guardamos el token (nuevo o refrescado) para la próxima ejecución.
        with open(TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())

    return creds


def _get_service():
    """Construye y devuelve el cliente autenticado de la API de Calendar."""
    creds = _get_credentials()
    return build("calendar", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# LECTURA DE EVENTOS
# ---------------------------------------------------------------------------

def get_busy_events(target_date: date) -> list[dict]:
    """
    Devuelve todos los eventos existentes en Google Calendar para el día
    solicitado, ya convertidos a un formato simple que rules_engine.py
    puede consumir directamente.

    Returns:
        Lista de dicts: [{"start": datetime, "end": datetime, "summary": str}, ...]
        Lista vacía si no hay eventos o si ocurre un error controlado.
    """
    day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=TIMEZONE)
    day_end = day_start + timedelta(days=1)

    try:
        service = _get_service()
        response = (
            service.events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except HttpError as error:
        logger.error("Error de la API de Google Calendar al leer eventos: %s", error)
        # Devolvemos lista vacía en lugar de tronar: es más seguro asumir
        # "no sabemos la disponibilidad" que romper el flujo del webhook.
        # Quien llame a esta función debería tratar esto como "no disponible
        # temporalmente" si lo desea, en vez de "todo el día libre".
        raise
    except Exception as error:
        logger.error("Error inesperado al consultar Google Calendar: %s", error)
        raise

    events = response.get("items", [])
    busy_list = []

    for event in events:
        parsed = _parse_event(event)
        if parsed is not None:
            busy_list.append(parsed)

    return busy_list


def _parse_event(event: dict) -> dict | None:
    """
    Convierte un evento crudo de la API de Google Calendar al formato
    simple que usa el resto del sistema: {id, start, end, summary}.

    Devuelve None si el evento viene con datos incompletos o mal
    formados, en vez de lanzar una excepción (así un solo evento corrupto
    no tumba una lista completa de resultados).
    """
    start_raw = event.get("start", {})
    end_raw = event.get("end", {})

    # Los eventos de "todo el día" usan la clave "date" en vez de
    # "dateTime". Los tratamos como si ocuparan todo el horario laboral.
    start_str = start_raw.get("dateTime") or start_raw.get("date")
    end_str = end_raw.get("dateTime") or end_raw.get("date")

    if not start_str or not end_str:
        return None

    try:
        if "T" in start_str:
            start_dt = datetime.fromisoformat(start_str)
        else:
            start_dt = datetime.fromisoformat(start_str).replace(tzinfo=TIMEZONE)

        if "T" in end_str:
            end_dt = datetime.fromisoformat(end_str)
        else:
            end_dt = datetime.fromisoformat(end_str).replace(tzinfo=TIMEZONE)
    except ValueError as exc:
        logger.warning("No se pudo parsear un evento (%s), se ignora: %s", event.get("id"), exc)
        return None

    return {
        "id": event.get("id"),
        "start": start_dt.astimezone(TIMEZONE),
        "end": end_dt.astimezone(TIMEZONE),
        "summary": event.get("summary", ""),
    }


# ---------------------------------------------------------------------------
# CREACIÓN DE CITAS
# ---------------------------------------------------------------------------

def create_appointment(target_date: date, start_time_str: str, client_name: str,
                       phone_number: str, description: str | None = None) -> dict:
    """
    Inserta una nueva cita de 1 hora en Google Calendar.

    Args:
        target_date: fecha de la cita.
        start_time_str: hora de inicio en formato "HH:MM" (ej. "10:15"),
                         tal como la devuelve rules_engine.generate_available_slots.
        client_name: nombre del cliente (para el título del evento).
        phone_number: número de WhatsApp del cliente (se guarda visible
                       en la descripción del evento).
        description: motivo o descripción de la cita que dio el cliente.

    Returns:
        dict con al menos {"id": str, "htmlLink": str} del evento creado.

    Raises:
        HttpError: si Google Calendar rechaza la solicitud.
        ValueError: si start_time_str no tiene un formato válido.
    """
    try:
        hour, minute = map(int, start_time_str.split(":"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Formato de hora inválido: '{start_time_str}' (se esperaba 'HH:MM')") from exc

    start_dt = datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=TIMEZONE)
    end_dt = start_dt + timedelta(hours=1)

    # Armamos la descripción del evento de forma estructurada para que sea
    # legible desde Google Calendar y también parseable si algún día se
    # necesita extraer el teléfono programáticamente.
    lineas_descripcion = [
        f"📱 WhatsApp: {phone_number}",
    ]
    if description:
        lineas_descripcion.append(f"📝 Motivo: {description}")
    lineas_descripcion.append("")
    lineas_descripcion.append("Agendado automáticamente vía WhatsApp Bot.")

    event_body = {
        "summary": f"Cita - {client_name}",
        "description": "\n".join(lineas_descripcion),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE_NAME},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE_NAME},
    }

    try:
        service = _get_service()
        created_event = service.events().insert(calendarId=CALENDAR_ID, body=event_body).execute()
    except HttpError as error:
        logger.error("Error de la API de Google Calendar al crear la cita: %s", error)
        raise

    logger.info("Cita creada correctamente: %s", created_event.get("htmlLink"))
    return created_event


# ---------------------------------------------------------------------------
# BÚSQUEDA DE CITAS DE UN CLIENTE (para cancelar / reprogramar)
# ---------------------------------------------------------------------------

def find_appointments_by_phone(phone_number: str, max_results: int = 5) -> list[dict]:
    """
    Busca las citas futuras de un cliente localizando su número de
    teléfono dentro de la descripción del evento (guardado ahí mismo por
    create_appointment).

    Nota: esto depende de que el número de teléfono coincida tal cual
    como Twilio lo manda en cada mensaje. Si el mismo cliente escribiera
    algún día desde un número distinto, esta búsqueda no encontraría sus
    citas anteriores — limitación aceptable para un MVP.

    Args:
        phone_number: número del cliente, ej. "+521234567890".
        max_results: máximo de citas futuras a devolver.

    Returns:
        Lista de dicts {id, start, end, summary}, ordenada por fecha
        ascendente (la más próxima primero). Lista vacía si no hay citas
        o si ocurre un error controlado.
    """
    now = datetime.now(TIMEZONE)

    try:
        service = _get_service()
        response = (
            service.events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=now.isoformat(),
                q=phone_number,
                singleEvents=True,
                orderBy="startTime",
                maxResults=max_results,
            )
            .execute()
        )
    except HttpError as error:
        logger.error("Error de la API de Google Calendar al buscar citas de %s: %s", phone_number, error)
        raise

    events = response.get("items", [])
    citas = []
    for event in events:
        parsed = _parse_event(event)
        # Filtro extra de seguridad: nos aseguramos de que el teléfono
        # realmente esté en la descripción, ya que "q" hace búsqueda de
        # texto libre y podría traer coincidencias parciales o de otros
        # campos del evento.
        if parsed is not None and phone_number in (event.get("description") or ""):
            citas.append(parsed)

    return citas


# ---------------------------------------------------------------------------
# REPROGRAMACIÓN Y CANCELACIÓN
# ---------------------------------------------------------------------------

def update_appointment(event_id: str, new_date: date, new_start_time_str: str) -> dict:
    """
    Mueve una cita existente a una nueva fecha/hora (reprogramación).
    Conserva la duración de 1 hora y el resto de los datos del evento
    (nombre del cliente, teléfono en la descripción, etc.).

    Args:
        event_id: ID del evento en Google Calendar a mover.
        new_date: nueva fecha de la cita.
        new_start_time_str: nueva hora de inicio "HH:MM".

    Returns:
        dict del evento actualizado.

    Raises:
        HttpError: si Google Calendar rechaza la solicitud (ej. el
                   evento ya no existe porque fue borrado manualmente).
        ValueError: si new_start_time_str no tiene un formato válido.
    """
    try:
        hour, minute = map(int, new_start_time_str.split(":"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Formato de hora inválido: '{new_start_time_str}' (se esperaba 'HH:MM')") from exc

    new_start_dt = datetime(new_date.year, new_date.month, new_date.day, hour, minute, tzinfo=TIMEZONE)
    new_end_dt = new_start_dt + timedelta(hours=1)

    body = {
        "start": {"dateTime": new_start_dt.isoformat(), "timeZone": TIMEZONE_NAME},
        "end": {"dateTime": new_end_dt.isoformat(), "timeZone": TIMEZONE_NAME},
    }

    try:
        service = _get_service()
        updated_event = service.events().patch(calendarId=CALENDAR_ID, eventId=event_id, body=body).execute()
    except HttpError as error:
        logger.error("Error de la API de Google Calendar al reprogramar el evento %s: %s", event_id, error)
        raise

    logger.info("Cita %s reprogramada correctamente a %s", event_id, new_start_dt.isoformat())
    return updated_event


def delete_appointment(event_id: str) -> None:
    """
    Cancela (borra) una cita existente en Google Calendar.

    Args:
        event_id: ID del evento a borrar.

    Raises:
        HttpError: si Google Calendar rechaza la solicitud. Un 404/410
                   (el evento ya no existe) también se propaga para que
                   conversation_manager.py pueda decidir cómo avisarle
                   al cliente en vez de asumir silenciosamente que se
                   canceló.
    """
    try:
        service = _get_service()
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
    except HttpError as error:
        logger.error("Error de la API de Google Calendar al cancelar el evento %s: %s", event_id, error)
        raise

    logger.info("Cita %s cancelada correctamente", event_id)


# ---------------------------------------------------------------------------
# PRUEBA RÁPIDA MANUAL (puedes borrar este bloque en producción)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    today = date.today()
    print(f"Consultando eventos de hoy ({today})...")
    try:
        events = get_busy_events(today)
        for ev in events:
            print(f"  - {ev['start']} a {ev['end']}: {ev['summary']}")
    except Exception as e:
        print(f"Error al consultar Google Calendar: {e}")
