"""
reminders.py
-------------
Envía el recordatorio de cita del día anterior a cada paciente agendado
para mañana.

Se dispara de dos formas (ver main.py):
  1. Automáticamente todos los días a una hora fija, si el proceso del
     bot corre 24/7 (ej. Render Starter).
  2. Vía el endpoint HTTP /tasks/send-reminders, para quien prefiera
     dispararlo con un cron externo (útil en el plan Free de Render, que
     se duerme y no puede confiar en un scheduler interno).
"""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import calendar_service
import whatsapp_client

logger = logging.getLogger("reminders")

TIMEZONE = ZoneInfo("America/Mexico_City")

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _format_fecha_legible(fecha: date) -> str:
    return f"{DIAS_SEMANA[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]}"


def enviar_recordatorios_de_manana() -> dict:
    """
    Busca todas las citas de mañana y les manda el recordatorio.

    Returns:
        dict con el resumen de la ejecución:
        {"fecha": "2026-07-16", "total_citas": 5, "enviados": 4, "fallidos": 1}
    """
    manana = datetime.now(TIMEZONE).date() + timedelta(days=1)
    fecha_legible = _format_fecha_legible(manana)

    try:
        citas = calendar_service.get_events_for_date(manana)
    except Exception as exc:
        logger.error("No se pudieron obtener las citas de mañana (%s): %s", manana, exc)
        return {"fecha": manana.isoformat(), "total_citas": 0, "enviados": 0, "fallidos": 0, "error": str(exc)}

    enviados = 0
    fallidos = 0

    for cita in citas:
        hora = cita["start"].strftime("%H:%M")
        exito = whatsapp_client.send_appointment_reminder(
            to_number=cita["telefono"],
            nombre=cita.get("nombre"),
            fecha_legible=fecha_legible,
            hora=hora,
        )
        if exito:
            enviados += 1
        else:
            fallidos += 1

    logger.info(
        "Recordatorios para %s: %d citas encontradas, %d enviados, %d fallidos",
        manana, len(citas), enviados, fallidos,
    )

    return {
        "fecha": manana.isoformat(),
        "total_citas": len(citas),
        "enviados": enviados,
        "fallidos": fallidos,
    }


def cancelar_citas_sin_pago() -> dict:
    """
    Cancela las citas marcadas [PENDIENTE PAGO] cuya fecha límite de pago
    ya venció, liberando el espacio para otros pacientes.

    Se intenta avisar al paciente por WhatsApp (best-effort: si la
    ventana de 24h de su última interacción ya cerró, el mensaje libre
    fallará y solo quedará registrado en logs — la fecha límite ya se le
    había comunicado al apartar la cita).

    Returns:
        dict con el resumen: {"canceladas": 2, "avisados": 1, "errores": 0}
    """
    try:
        vencidas = calendar_service.find_expired_pending_appointments()
    except Exception as exc:
        logger.error("No se pudieron buscar citas pendientes vencidas: %s", exc)
        return {"canceladas": 0, "avisados": 0, "errores": 1, "error": str(exc)}

    canceladas = 0
    avisados = 0
    errores = 0

    for cita in vencidas:
        try:
            calendar_service.delete_appointment(cita["id"])
            canceladas += 1
            logger.info(
                "Cita %s cancelada por falta de pago (límite era %s)",
                cita["id"], cita["fecha_limite"],
            )
        except Exception as exc:
            logger.error("No se pudo cancelar la cita vencida %s: %s", cita["id"], exc)
            errores += 1
            continue

        if cita.get("telefono"):
            aviso = (
                "Hola 🙂 Te escribimos del consultorio de la Dra. Midori "
                "Muraoka. No recibimos el depósito de confirmación de tu "
                f"cita del {_format_fecha_legible(cita['start'].date())} a las "
                f"{cita['start'].strftime('%H:%M')} dentro del plazo, así que "
                "el espacio se liberó. Si aún te interesa, con gusto te "
                "buscamos un nuevo horario — solo escríbenos."
            )
            if whatsapp_client.send_whatsapp_message(cita["telefono"], aviso):
                avisados += 1

    resultado = {"canceladas": canceladas, "avisados": avisados, "errores": errores}
    logger.info("Expiración de citas sin pago: %s", resultado)
    return resultado


# ---------------------------------------------------------------------------
# PRUEBA RÁPIDA MANUAL (puedes borrar este bloque en producción)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    resultado = enviar_recordatorios_de_manana()
    print(resultado)
