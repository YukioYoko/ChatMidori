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
| `main.py` | Servidor FastAPI, recibe el webhook de Twilio |
| `conversation_manager.py` | Máquina de estados por conversación |
| `nlu_service.py` | Interpreta lenguaje natural con Claude Haiku 4.5 |
| `rules_engine.py` | Lógica pura de horarios disponibles |
| `calendar_service.py` | Lectura/escritura en Google Calendar |
| `whatsapp_client.py` | Envío/recepción de mensajes vía Twilio |

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

Expón el puerto con [ngrok](https://ngrok.com) para que Twilio pueda llegar a tu máquina:

```bash
ngrok http 8000
```

Copia la URL https que te da ngrok y configúrala en el Console de Twilio:
**Messaging → Try it out → Sandbox settings → "WHEN A MESSAGE COMES IN"**,
agregando `/webhook/whatsapp` al final (ej. `https://abcd1234.ngrok-free.app/webhook/whatsapp`).

## Estado del proyecto

Funcional para el flujo principal: saludo, agendado de citas con lenguaje
natural, selección de horario y confirmación. Cancelación/reprogramación
automática de citas queda pendiente como siguiente iteración.
