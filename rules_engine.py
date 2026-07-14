"""
rules_engine.py
----------------
Lógica pura de negocio (sin dependencias de red ni APIs externas).

Recibe:
  - La fecha solicitada por el cliente.
  - La lista de eventos "ocupados" que calendar_service.py obtuvo de Google Calendar.

Devuelve:
  - Una lista de strings con las horas disponibles, ej: ["09:00", "10:15", "11:30"]

Toda la configuración de reglas de negocio vive aquí arriba, en constantes,
para que sea fácil de ajustar sin tocar el resto del sistema.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# CONFIGURACIÓN DE REGLAS DE NEGOCIO
# ---------------------------------------------------------------------------

# Zona horaria del negocio. Modifica según tu ubicación real.
TIMEZONE = ZoneInfo("America/Mexico_City")

# Horario laboral por día de la semana.
# Python: Monday = 0 ... Sunday = 6
# Formato: dia_semana -> (hora_apertura, minuto_apertura, hora_cierre, minuto_cierre)
# Basado en el horario del consultorio de la Dra. Midori Muraoka:
#   Lunes a Viernes: 4:00 PM - 8:00 PM
#   Sábado:          9:30 AM - 2:00 PM
#   Domingo:         cerrado
BUSINESS_HOURS = {
    0: (16, 0, 20, 0),   # Lunes
    1: (16, 0, 20, 0),   # Martes
    2: (16, 0, 20, 0),   # Miércoles
    3: (16, 0, 20, 0),   # Jueves
    4: (16, 0, 20, 0),   # Viernes
    5: (9, 30, 14, 0),   # Sábado
    # 6 (Domingo) no aparece -> cerrado
}

# Duración fija de cada cita (30 minutos).
APPOINTMENT_DURATION = timedelta(minutes=30)

# Espacio obligatorio entre el fin de una cita y el inicio de la siguiente.
# El consultorio no requiere buffer entre citas.
BUFFER_TIME = timedelta(minutes=0)

# Anticipación mínima para reservar el mismo día.
MIN_ADVANCE_NOTICE = timedelta(hours=3)

# Granularidad con la que probamos posibles horas de inicio.
# Ajustada a 30 min para que los slots caigan en horas y medias horas
# (16:00, 16:30, 17:00, ...) — congruente con la duración de la cita.
SLOT_GRANULARITY = timedelta(minutes=30)

# Si el título/summary de un evento de Google Calendar contiene alguna de
# estas palabras (sin importar mayúsculas/minúsculas), ese rango se
# considera ocupado de forma absoluta, sin importar la duración del evento.
BLOCKED_KEYWORDS = ["BLOQUEADO", "ALMUERZO", "PRIVADO"]


# ---------------------------------------------------------------------------
# FUNCIONES INTERNAS
# ---------------------------------------------------------------------------

def _is_blocked_event(summary: str) -> bool:
    """Determina si el título de un evento lo marca como bloqueo especial."""
    if not summary:
        return False
    summary_upper = summary.upper()
    return any(keyword in summary_upper for keyword in BLOCKED_KEYWORDS)


def _normalize_busy_intervals(busy_events: list[dict]) -> list[tuple[datetime, datetime]]:
    """
    Convierte la lista de eventos crudos de Google Calendar en una lista de
    intervalos (inicio, fin) ya expandidos con el buffer de 15 minutos.

    Se espera que cada evento en busy_events sea un dict con:
        {
            "start": datetime (con tzinfo),
            "end": datetime (con tzinfo),
            "summary": str
        }

    Los eventos marcados como BLOQUEADO / ALMUERZO / PRIVADO se tratan igual
    que cualquier otro evento ocupado (se descartan por completo,
    independientemente de su duración real).
    """
    intervals = []
    for event in busy_events:
        start = event.get("start")
        end = event.get("end")
        summary = event.get("summary", "")

        if start is None or end is None:
            # Evento con datos incompletos: lo ignoramos de forma segura
            # en lugar de tronar el flujo completo.
            continue

        # Nota: _is_blocked_event no cambia el comportamiento (todo evento
        # bloquea), pero se deja explícito para trazabilidad/logs si se
        # desea distinguir el motivo del bloqueo más adelante.
        _is_blocked_event(summary)

        # Aplicamos el buffer de amortiguación ANTES del inicio y DESPUÉS
        # del fin del evento, para garantizar los 15 min libres entre citas.
        buffered_start = start - BUFFER_TIME
        buffered_end = end + BUFFER_TIME
        intervals.append((buffered_start, buffered_end))

    return intervals


def _overlaps(slot_start: datetime, slot_end: datetime,
              busy_start: datetime, busy_end: datetime) -> bool:
    """True si el rango [slot_start, slot_end) se traslapa con [busy_start, busy_end)."""
    return slot_start < busy_end and slot_end > busy_start


# ---------------------------------------------------------------------------
# FUNCIÓN PRINCIPAL
# ---------------------------------------------------------------------------

def generate_available_slots(requested_date: date, busy_events: list[dict]) -> list[str]:
    """
    Punto de entrada del motor de reglas.

    Args:
        requested_date: fecha (date) para la que el cliente quiere agendar.
        busy_events: lista de eventos ocupados obtenidos de
                     calendar_service.get_busy_events(requested_date).

    Returns:
        Lista de strings "HH:MM" con los horarios de inicio disponibles
        para una cita de 1 hora, ya validados contra todas las reglas.
    """
    weekday = requested_date.weekday()

    # 1. Domingo cerrado / día sin horario configurado.
    if weekday not in BUSINESS_HOURS:
        return []

    open_hour, open_minute, close_hour, close_minute = BUSINESS_HOURS[weekday]

    day_start = datetime(
        requested_date.year, requested_date.month, requested_date.day,
        open_hour, open_minute, tzinfo=TIMEZONE
    )
    day_end = datetime(
        requested_date.year, requested_date.month, requested_date.day,
        close_hour, close_minute, tzinfo=TIMEZONE
    )

    # 2. Intervalos ocupados ya expandidos con buffer.
    busy_intervals = _normalize_busy_intervals(busy_events)

    # 3. Anticipación mínima: si la fecha solicitada es HOY, no se puede
    #    agendar si faltan menos de 3 horas para el horario propuesto.
    now = datetime.now(TIMEZONE)
    is_today = requested_date == now.date()
    earliest_allowed = now + MIN_ADVANCE_NOTICE if is_today else None

    # 4. Barremos candidatos de inicio cada SLOT_GRANULARITY minutos.
    available_slots: list[str] = []
    candidate_start = day_start

    while candidate_start + APPOINTMENT_DURATION <= day_end:
        candidate_end = candidate_start + APPOINTMENT_DURATION

        # Regla de anticipación mínima (solo aplica si es el mismo día).
        if earliest_allowed is not None and candidate_start < earliest_allowed:
            candidate_start += SLOT_GRANULARITY
            continue

        # Regla de choque con eventos ocupados (ya incluye el buffer).
        conflict = any(
            _overlaps(candidate_start, candidate_end, busy_start, busy_end)
            for busy_start, busy_end in busy_intervals
        )

        if not conflict:
            available_slots.append(candidate_start.strftime("%H:%M"))

        candidate_start += SLOT_GRANULARITY

    return available_slots


# ---------------------------------------------------------------------------
# PRUEBA RÁPIDA MANUAL (puedes borrar este bloque en producción)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Ejemplo de eventos ocupados ficticios para probar el motor sin tocar
    # la API real de Google.
    test_date = date.today()

    fake_busy_events = [
        {
            "start": datetime(test_date.year, test_date.month, test_date.day, 10, 0, tzinfo=TIMEZONE),
            "end": datetime(test_date.year, test_date.month, test_date.day, 11, 0, tzinfo=TIMEZONE),
            "summary": "Consulta con cliente",
        },
        {
            "start": datetime(test_date.year, test_date.month, test_date.day, 13, 0, tzinfo=TIMEZONE),
            "end": datetime(test_date.year, test_date.month, test_date.day, 14, 0, tzinfo=TIMEZONE),
            "summary": "ALMUERZO equipo",
        },
    ]

    slots = generate_available_slots(test_date, fake_busy_events)
    print(f"Horarios disponibles para {test_date}: {slots}")
