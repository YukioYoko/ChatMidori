# WhatsApp Appointment Bot

Chatbot en Python que gestiona citas de forma automática por WhatsApp:
entiende lenguaje natural con Claude (Haiku 4.5), aplica reglas de negocio
sobre disponibilidad, y crea las citas directamente en Google Calendar.

## Arquitectura

```
Cliente WhatsApp
      │
      ▼
Twilio (canal WhatsApp) ── webhook POST ──▶ main.py (FastAPI)
                                                  │
                                                  ▼
                                    conversation_manager.py (estado)
                                        │           │
                                        ▼           ▼
                              nlu_service.py   rules_engine.py
                              (Claude Haiku)   (reglas de negocio)
                                                     │
                                                     ▼
                                          calendar_service.py
                                          (Google Calendar API)
```

## Archivos del proyecto

| Archivo | Responsabilidad |
|---|---|
| `main.py` | Servidor FastAPI, recibe el webhook de Twilio, dispara recordatorios |
| `conversation_manager.py` | Máquina de estados por conversación |
| `nlu_service.py` | Interpreta lenguaje natural con Claude Haiku 4.5 |
| `rules_engine.py` | Lógica pura de horarios disponibles |
| `calendar_service.py` | Lectura/escritura en Google Calendar |
| `whatsapp_client.py` | Envío/recepción de mensajes vía Twilio |
| `reminders.py` | Recordatorio automático de cita el día anterior |
| `business_config.py` | Personalidad, tono y contexto del consultorio |

## Recordatorios automáticos (día anterior)

El bot manda un recordatorio de WhatsApp el día antes de cada cita.
Como es un mensaje que **el negocio inicia** (no una respuesta al
paciente), WhatsApp exige una plantilla ("Content Template") aprobada
por Meta — no se puede mandar como texto libre.

**Configuración (una sola vez):**
1. Twilio Console → Messaging → Content Template Builder → crea una
   plantilla categoría **Utility**, en español, con 3 variables (nombre,
   fecha, hora). El texto sugerido está en `whatsapp_client.py`.
2. Espera la aprobación de Meta (usualmente minutos a pocas horas).
3. Copia el Content SID (`HX...`) a la variable de entorno
   `TWILIO_REMINDER_CONTENT_SID`.

**Cómo se dispara:**
- Si el bot corre 24/7 (ej. Render Starter): automático, todos los días
  a la hora que definas en `REMINDER_HOUR` (default 10am).
- Si usas un plan que se duerme (ej. Render Free): desactiva el
  scheduler interno (`REMINDER_SCHEDULER_ENABLED=false`) y usa un cron
  externo gratuito (cron-job.org, Render Cron Jobs) que le pegue una vez
  al día a `POST /tasks/send-reminders` con el header
  `X-Reminder-Secret: <tu REMINDER_SECRET>`.

## Instalación

```bash
python3 -m venv venv
source venv/bin/activate      # En Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Configuración

1. Copia `.env.example` a `.env` y llena tus credenciales:
   ```bash
   cp .env.example .env
   ```
2. **Anthropic**: crea tu API key en [console.anthropic.com](https://console.anthropic.com).
3. **Twilio**: crea una cuenta en [twilio.com](https://www.twilio.com/try-twilio) y activa el
   sandbox de WhatsApp (Messaging → Try it out → Send a WhatsApp message).
4. **Google Calendar**: crea credenciales OAuth 2.0 (tipo "Desktop App") en
   [Google Cloud Console](https://console.cloud.google.com), descarga el
   JSON y guárdalo como `credentials.json` en la raíz del proyecto.
5. Carga las variables de entorno antes de correr el bot (o usa
   `python-dotenv` si prefieres cargarlas desde `.env` automáticamente).

## Correr el proyecto localmente

```bash
uvicorn main:app --reload --port 8000
```

Expón el puerto con un túnel para que Twilio pueda llegar a tu máquina. Puedes
usar [ngrok](https://ngrok.com) o [Cloudflare Tunnel](https://github.com/cloudflare/cloudflared/releases/latest)
(este último no requiere cuenta para pruebas rápidas):

```bash
cloudflared tunnel --url http://localhost:8000
```

Copia la URL https que te da el túnel y configúrala en el Console de Twilio:
**Messaging → Try it out → Sandbox settings → "WHEN A MESSAGE COMES IN"**,
agregando `/webhook/whatsapp` al final (ej. `https://abcd1234.trycloudflare.com/webhook/whatsapp`).

## Probar el bot de punta a punta (checklist)

1. **Variables de entorno**: copia `.env.example` a `.env` y llena tus
   credenciales reales. `main.py` las carga automáticamente al arrancar
   (vía `python-dotenv`) — no necesitas exportarlas manualmente en cada
   terminal nueva.
2. **Google Calendar**: coloca tu `credentials.json` en la raíz del
   proyecto y corre una vez `python calendar_service.py` directamente.
   Se abrirá tu navegador para autorizar el acceso y se generará
   `token.json` automáticamente.
3. **Arranca el servidor**: `uvicorn main:app --reload --port 8000`.
   Deberías ver el log `Application startup complete` sin errores.
4. **Levanta el túnel**: `cloudflared tunnel --url http://localhost:8000`
   (o `ngrok http 8000`). Copia la URL https que te da.
5. **Configura el webhook en Twilio** con esa URL + `/webhook/whatsapp`
   (ver arriba).
6. **Únete al sandbox**: desde tu celular, mándale al número del sandbox
   de Twilio el código "join palabra-clave" que te dio el Console.
7. **Manda un mensaje de prueba**: "hola" debería responder con el
   saludo; "quiero una cita mañana en la tarde" debería mostrarte
   horarios disponibles.
8. **Revisa la terminal** donde corre uvicorn — ahí verás los logs de
   cada mensaje entrante y cualquier error si algo falla.
9. **Confirma en Google Calendar** que la cita se creó correctamente
   después de elegir un horario.

> **Nota:** si usas un túnel gratuito (cloudflared sin cuenta, o el
> sandbox de ngrok), la URL cambia cada vez que lo reinicias — tendrás
> que actualizar el webhook en Twilio cada vez. Para una URL fija sin
> este problema, considera desplegar el bot en un hosting como Render
> o Railway más adelante.

## Estado del proyecto

Funcional para el flujo principal: saludo, agendado de citas con lenguaje
natural, selección de horario y confirmación. Cancelación/reprogramación
automática de citas queda pendiente como siguiente iteración.
