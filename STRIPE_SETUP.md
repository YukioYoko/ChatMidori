# Configuración de Stripe para el cobro del depósito 💳

Esta rama (`pago-stripe`) reemplaza la transferencia bancaria manual por
un **link de pago de Stripe**: el paciente paga con tarjeta o en OXXO, y
la cita se **confirma sola** — la doctora no tiene que revisar depósitos
ni tocar el calendario.

## ¿Por qué Stripe y no PayPal?

- **OXXO**: muchos pacientes en México prefieren pagar en efectivo.
  Stripe genera una ficha de OXXO desde el mismo link; PayPal no.
- **Sin cuenta**: el paciente paga directo con su tarjeta, sin
  registrarse en nada. PayPal empuja a crear/usar cuenta.
- **Webhooks confiables**: es lo que permite la confirmación automática.
- Comisión en México: ~3.6% + $3 MXN por transacción con tarjeta
  (~$10 MXN sobre un depósito de $200). OXXO cobra ~3.6% + IVA.

## Cómo queda el flujo completo

```
Paciente agenda ──► Cita creada [PENDIENTE PAGO] en Google Calendar
                    └─► Bot manda link de pago por WhatsApp
Paciente paga  ──► Stripe notifica al bot (webhook)
                    ├─► Bot quita [PENDIENTE PAGO] del evento
                    └─► Bot confirma al paciente por WhatsApp
No paga a tiempo ─► Job de expiración cancela la cita y libera el espacio
```

---

## Paso 1 — Crear la cuenta de Stripe

1. Regístrate en [dashboard.stripe.com/register](https://dashboard.stripe.com/register)
   con los datos del consultorio.
2. Completa la activación de la cuenta (datos fiscales y bancarios de
   México) para poder recibir pagos reales. Mientras tanto puedes probar
   todo en **modo test**.

## Paso 2 — Activar los métodos de pago

1. En el Dashboard: **Settings → Payments → Payment methods**.
2. Activa **Cards** (viene activo) y **OXXO**.
   - OXXO requiere que la cuenta esté configurada en México (MXN).
   - Los pagos OXXO tardan desde minutos hasta 1-2 días en acreditarse
     después de que el paciente paga en tienda — el bot ya maneja esto
     (la confirmación llega cuando el pago se acredita de verdad).

## Paso 3 — Obtener la clave secreta

1. Dashboard → **Developers → API keys**.
2. Copia la **Secret key**:
   - Modo prueba: empieza con `sk_test_...`
   - Modo producción: empieza con `sk_live_...`
3. Ponla en tu `.env` (local) o en las variables de entorno del hosting:
   ```
   STRIPE_SECRET_KEY=sk_test_xxxxxxxxxxxxxxxx
   ```

## Paso 4 — Configurar el webhook

Esto es lo que permite que la cita se confirme sola al recibir el pago.

1. Dashboard → **Developers → Webhooks → Add endpoint**.
2. **Endpoint URL**: la URL pública de tu bot + `/webhook/stripe`
   - En producción: `https://tu-bot.onrender.com/webhook/stripe`
   - En pruebas locales: la URL de tu túnel (cloudflared/ngrok) +
     `/webhook/stripe` — recuerda actualizarla si el túnel cambia.
3. **Events to send** — selecciona exactamente estos dos:
   - `checkout.session.completed`
   - `checkout.session.async_payment_succeeded`
4. Guarda y copia el **Signing secret** (empieza con `whsec_...`):
   ```
   STRIPE_WEBHOOK_SECRET=whsec_xxxxxxxxxxxxxxxx
   ```

> ⚠️ Sin `STRIPE_WEBHOOK_SECRET`, el bot rechaza todos los webhooks por
> seguridad (no puede verificar que realmente vengan de Stripe).

## Paso 5 — Instalar la dependencia y probar

```bash
pip install -r requirements.txt   # ya incluye "stripe" en esta rama
```

**Prueba end-to-end en modo test:**

1. Levanta el bot y el túnel como siempre.
2. Agrega tu propio número a la lista negra para forzar el flujo de
   depósito (o usa la rama de depósito obligatorio):
   ```bash
   curl -X POST http://localhost:8000/admin/blacklist \
        -H "X-Admin-Secret: TU_SECRETO" \
        -H "Content-Type: application/json" \
        -d '{"phone": "+521XXXXXXXXXX", "motivo": "prueba"}'
   ```
3. Agenda una cita por WhatsApp — recibirás el link de pago.
4. Paga con la tarjeta de prueba de Stripe: `4242 4242 4242 4242`,
   cualquier fecha futura y cualquier CVC.
5. En segundos debe: quitarse `[PENDIENTE PAGO]` del evento en Google
   Calendar y llegarte la confirmación por WhatsApp.
6. Para probar OXXO en modo test, elige OXXO en el checkout — Stripe te
   da un botón para simular que la ficha fue pagada.

## Paso 6 — Pasar a producción

1. Cambia `STRIPE_SECRET_KEY` a la clave `sk_live_...`.
2. Crea un **segundo webhook** en modo live (los webhooks de test y live
   son independientes) apuntando a tu URL de producción, y actualiza
   `STRIPE_WEBHOOK_SECRET` con el nuevo `whsec_...`.
3. Haz un pago real pequeño de prueba antes de anunciarlo a pacientes.

---

## Detalles de comportamiento que conviene conocer

- **Fallback automático**: si `STRIPE_SECRET_KEY` no está configurada o
  Stripe falla al crear el link, el bot cae de vuelta al mensaje de
  transferencia bancaria manual (el comportamiento de la rama `main`).
  El sistema nunca se queda sin poder apartar la cita.
- **Ficha OXXO generada ≠ pagada**: cuando el paciente genera su ficha,
  Stripe manda un evento que el bot ignora correctamente. La cita solo
  se confirma cuando el pago se acredita.
- **Pago después del plazo** (caso raro): si el paciente paga minutos
  después de que el job de expiración canceló su cita, el bot le avisa
  por WhatsApp que escriba para reagendar o gestionar su reembolso, y lo
  deja registrado en los logs con prioridad alta. El reembolso se hace
  desde el Dashboard de Stripe (Payments → el pago → Refund).
- **El link se desactiva al pagarse** — no puede reutilizarse para
  pagar dos veces.
- **El monto** se configura en `business_config.py`
  (`DEPOSIT_AMOUNT_MXN`) — un solo lugar, igual que en `main`.

## Costos de esta modalidad

| Concepto | Costo |
|---|---|
| Cuenta de Stripe | Gratis (sin mensualidad) |
| Comisión por depósito de $200 con tarjeta | ~$10.20 MXN |
| Comisión por depósito de $200 vía OXXO | ~$8.40 MXN + IVA |
| Todo lo demás del bot | Igual que en `main` |

La comisión la absorbe el consultorio o se ajusta el monto del depósito
para compensarla — decisión de negocio.
