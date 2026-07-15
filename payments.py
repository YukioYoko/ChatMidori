"""
payments.py
------------
Integración con Stripe para el cobro del depósito de confirmación.

Flujo completo (100% automático):
  1. El paciente agenda y requiere depósito -> el bot crea un Payment
     Link de Stripe con el monto configurado y se lo manda por WhatsApp.
  2. El paciente paga con tarjeta u OXXO desde ese link.
  3. Stripe notifica al bot vía webhook (/webhook/stripe en main.py).
  4. El bot quita "[PENDIENTE PAGO]" del evento en Google Calendar y le
     confirma al paciente por WhatsApp — sin intervención de la doctora.

Configuración necesaria (ver STRIPE_SETUP.md para el paso a paso):
  - STRIPE_SECRET_KEY: tu clave secreta de Stripe (sk_test_... / sk_live_...)
  - STRIPE_WEBHOOK_SECRET: el secreto del endpoint de webhook (whsec_...)

Si STRIPE_SECRET_KEY no está configurada, el sistema cae de vuelta al
modo de transferencia bancaria manual (el de la rama main).
"""

import os
import logging
from datetime import datetime

import stripe

import business_config

logger = logging.getLogger("payments")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")


def stripe_esta_configurado() -> bool:
    """True si hay clave de Stripe; si no, se usa transferencia manual."""
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def crear_link_de_pago(event_id: str, phone_number: str, nombre: str,
                       fecha_legible: str, hora: str) -> str | None:
    """
    Crea un Payment Link de Stripe para el depósito de esta cita.

    Se usa Payment Link (y no Checkout Session) porque no expira — el
    paciente tiene 2 días hábiles para pagar y una Checkout Session
    caduca a las 24 horas como máximo. El link se desactiva desde el
    webhook una vez pagado, para que no pueda reutilizarse.

    Los metadatos (event_id, phone_number) viajan con el pago y son lo
    que permite al webhook saber QUÉ cita confirmar y a QUIÉN avisar.

    Returns:
        La URL del link de pago, o None si Stripe falló (el llamador
        decide el fallback).
    """
    try:
        # Creamos el precio al vuelo con el monto configurado en
        # business_config — así el monto vive en un solo lugar del código.
        price = stripe.Price.create(
            currency="mxn",
            unit_amount=business_config.DEPOSIT_AMOUNT_MXN * 100,  # centavos
            product_data={
                "name": f"Depósito de confirmación — cita {fecha_legible} {hora}",
            },
        )

        link = stripe.PaymentLink.create(
            line_items=[{"price": price.id, "quantity": 1}],
            metadata={
                "event_id": event_id,
                "phone_number": phone_number,
                "nombre": nombre or "",
            },
            after_completion={
                "type": "hosted_confirmation",
                "hosted_confirmation": {
                    "custom_message": (
                        "¡Gracias! Tu pago fue recibido. En unos momentos "
                        "recibirás la confirmación de tu cita por WhatsApp. 🙂"
                    ),
                },
            },
        )
        logger.info("Payment Link creado para el evento %s: %s", event_id, link.id)
        return link.url

    except stripe.error.StripeError as exc:
        logger.error("Stripe falló al crear el link de pago: %s", exc)
        return None
    except Exception as exc:
        logger.error("Error inesperado creando link de pago: %s", exc)
        return None


def verificar_webhook(payload: bytes, signature_header: str):
    """
    Valida que el webhook realmente venga de Stripe (firma criptográfica)
    y devuelve el evento parseado.

    Raises:
        ValueError / stripe.error.SignatureVerificationError si la firma
        es inválida — main.py responde 400 en ese caso.
    """
    return stripe.Webhook.construct_event(
        payload, signature_header, STRIPE_WEBHOOK_SECRET
    )


def procesar_evento_de_pago(event) -> dict | None:
    """
    Procesa un evento de webhook de Stripe. Devuelve los metadatos de la
    cita a confirmar si el evento representa un pago completado, o None
    si el evento no nos interesa.

    Eventos que confirman la cita:
      - checkout.session.completed con payment_status == "paid"
        (pago con tarjeta: inmediato)
      - checkout.session.async_payment_succeeded
        (pago con OXXO: el paciente pagó en tienda, tarda horas/días)

    Nota importante sobre OXXO: cuando el paciente genera su ficha de
    OXXO, llega un checkout.session.completed con payment_status
    "unpaid" — eso NO confirma la cita, solo significa que generó la
    ficha. La confirmación real llega con async_payment_succeeded.
    """
    tipo = event["type"]
    session = event["data"]["object"]

    pago_confirmado = (
        (tipo == "checkout.session.completed" and session.get("payment_status") == "paid")
        or tipo == "checkout.session.async_payment_succeeded"
    )

    if not pago_confirmado:
        logger.info("Evento de Stripe ignorado (no es pago completado): %s", tipo)
        return None

    metadata = session.get("metadata") or {}
    event_id = metadata.get("event_id")
    phone_number = metadata.get("phone_number")

    if not event_id:
        logger.warning("Pago recibido sin event_id en metadata — no se puede asociar a una cita.")
        return None

    # Desactivamos el Payment Link para que no pueda reutilizarse.
    payment_link_id = session.get("payment_link")
    if payment_link_id:
        try:
            stripe.PaymentLink.modify(payment_link_id, active=False)
        except stripe.error.StripeError as exc:
            # No es crítico: el pago ya entró. Solo se loguea.
            logger.warning("No se pudo desactivar el Payment Link %s: %s", payment_link_id, exc)

    return {
        "event_id": event_id,
        "phone_number": phone_number,
        "nombre": metadata.get("nombre") or None,
    }
