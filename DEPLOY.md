# Guía de despliegue: de local a la nube 🚀

Esta guía explica cómo migrar el bot de tu computadora a un servicio de
hosting, **gastando lo mínimo posible y sin sorpresas**. Está escrita
específicamente para este proyecto — incluye los detalles que romperían
el bot si no se atienden (credenciales de Google, webhook de Twilio, etc.).

---

## 1. ¿Por qué migrar?

Corriendo en local dependes de: tu computadora encendida 24/7, el túnel
(cloudflared/ngrok) activo, y de actualizar la URL del webhook en Twilio
cada vez que el túnel se reinicia. En la nube obtienes una **URL fija
permanente** y el bot atiende solo, siempre.

---

## 2. Comparativa de opciones (precios reales 2026)

| Plataforma | Costo mensual | Siempre encendido | Notas |
|---|---|---|---|
| **Render (plan Free)** | **$0 USD** | ⚠️ No — se duerme tras 15 min sin tráfico | El único free tier real que queda. No pide tarjeta. |
| **Render (plan Starter)** | $7 USD | ✅ Sí | Precio fijo, sin sorpresas. 512 MB RAM. |
| **Railway (plan Hobby)** | $5 USD + uso (~$6-9 total) | ✅ Sí | Ya no tiene plan gratis. Cobra por uso: la factura varía. |
| **Fly.io** | ~$2-6 USD | ✅ Sí | El más barato en teoría, pero con costos ocultos frecuentes (IP dedicada +$2, volúmenes, egress). Requiere Docker y más configuración. |
| **VPS (Hetzner/DigitalOcean)** | $4-6 USD | ✅ Sí | Tú administras todo: seguridad, TLS, actualizaciones. No recomendado si no quieres hacer DevOps. |

### Recomendación para este bot

**Empieza con Render Free ($0) y sube a Starter ($7) cuando el bot ya
esté atendiendo pacientes reales.** Razones:

1. Es el único free tier real que queda en 2026 y no pide tarjeta.
2. El "sueño" del plan gratis tiene una solución aceptable para probar
   (ver sección 5), y el upgrade a $7 fijos es un clic — sin cambiar nada
   del código.
3. Railway es buena alternativa (~$5-9), pero su cobro por uso hace la
   factura menos predecible, que es justo lo que quieres evitar.

⚠️ **Advertencia importante sobre el plan Free**: cuando el servicio se
duerme, la primera petición tarda 30-60 segundos en despertar. **Twilio
corta los webhooks a los ~15 segundos**, así que un bot dormido puede
perder el primer mensaje de un paciente. Para pruebas está bien; para
producción con pacientes reales, usa el plan Starter ($7) o el truco de
la sección 5.

---

## 3. Preparación (una sola vez, en tu máquina)

### 3.1 El detalle que rompería todo: las credenciales de Google

En la nube, el disco del servidor se **borra en cada redeploy**. Por eso
`token.json` no puede vivir como archivo — el código ya está preparado
para leerlo desde una variable de entorno:

1. En tu máquina, asegúrate de que el bot ya funciona localmente (o sea,
   que `token.json` existe y es válido).
2. Abre `token.json` y copia **todo su contenido** (es una sola línea de
   JSON larga).
3. Ese contenido será el valor de la variable `GOOGLE_TOKEN_JSON` en el
   hosting (paso 4.3). El refresh token no caduca con el uso normal, así
   que esto se configura una sola vez.

### 3.2 Verifica que los secretos NO estén en el repo

```bash
git status
```
`credentials.json`, `token.json` y `.env` **no deben aparecer** (ya están
en `.gitignore`). En la nube todo se pasa por variables de entorno.

---

## 4. Despliegue en Render, paso a paso

### 4.1 Crear el servicio

1. Crea una cuenta en [render.com](https://render.com) (con tu GitHub es
   un clic, y el plan Free no pide tarjeta).
2. **New → Web Service** → conecta el repositorio `ChatMidori`.
3. Configuración:
   - **Region**: Oregon (US West) — la más cercana a México disponible.
   - **Branch**: `main`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: Free (o Starter si ya vas en serio)

### 4.2 ¿Por qué `$PORT` y `--host 0.0.0.0`?

Render (y casi cualquier nube) asigna el puerto dinámicamente vía la
variable `PORT`, y el servidor debe escuchar en todas las interfaces
(`0.0.0.0`), no solo en localhost. Sin esto el deploy "funciona" pero
nadie puede llegarle.

### 4.3 Variables de entorno

En la pestaña **Environment** del servicio, agrega:

| Variable | Valor |
|---|---|
| `ANTHROPIC_API_KEY` | tu key de console.anthropic.com |
| `TWILIO_ACCOUNT_SID` | tu SID de Twilio |
| `TWILIO_AUTH_TOKEN` | tu auth token de Twilio |
| `TWILIO_WHATSAPP_NUMBER` | `whatsapp:+14155238886` (sandbox) o tu número real |
| `GOOGLE_TOKEN_JSON` | el contenido completo de tu `token.json` (paso 3.1) |
| `VALIDATE_TWILIO_SIGNATURE` | `true` ← actívala en producción |

> 💡 En producción activa `VALIDATE_TWILIO_SIGNATURE=true`: tu URL será
> pública y fija, y sin la validación cualquiera que la descubra podría
> mandarle webhooks falsos a tu bot.

### 4.4 Actualizar el webhook en Twilio

Al terminar el deploy, Render te da una URL fija tipo
`https://chatmidori.onrender.com`. Ve al Console de Twilio →
**Messaging → Sandbox settings → "WHEN A MESSAGE COMES IN"** y pon:

```
https://chatmidori.onrender.com/webhook/whatsapp
```

Esto se hace **una sola vez** — se acabó el andar actualizando URLs de
túneles. 🎉

### 4.5 Probar

1. Abre `https://chatmidori.onrender.com/` en el navegador — debe
   responder `{"status": "ok", ...}`.
2. Manda un "hola" por WhatsApp al número del sandbox.
3. Revisa los logs en el dashboard de Render si algo no responde.

---

## 5. Truco para el plan Free: mantenerlo despierto

El plan Free duerme el servicio tras 15 minutos sin tráfico. Para un bot
de citas, un "ping" periódico lo mantiene despierto:

1. Crea una cuenta gratis en [uptimerobot.com](https://uptimerobot.com).
2. Agrega un monitor HTTP(s) apuntando a
   `https://chatmidori.onrender.com/` cada **5 minutos**.
3. Listo: el bot recibe una visita cada 5 min y nunca duerme, y de paso
   te avisa por correo si el servicio se cae.

**Limitación honesta**: Render Free da 750 horas de instancia al mes
(suficiente para un servicio 24/7) pero con recursos compartidos y sin
garantías. Para un consultorio con pacientes reales, los $7/mes del plan
Starter son la opción tranquila — sale más caro un paciente perdido que
el hosting.

---

## 6. Diferencias entre local y nube (resumen)

| Aspecto | Local | Nube (Render) |
|---|---|---|
| URL del webhook | Cambia con cada túnel | Fija para siempre |
| `token.json` | Archivo en disco | Variable `GOOGLE_TOKEN_JSON` |
| `credentials.json` | Archivo en disco | Ya no se necesita (el token ya está emitido) |
| `.env` | Archivo local | Variables en el dashboard |
| Encendido | Depende de tu PC | 24/7 (Starter) o con ping (Free) |
| Deploy | `git pull` + reiniciar | `git push` → deploy automático |

---

## 7. Nota sobre el estado de las conversaciones

Las conversaciones a medias se guardan **en memoria** (`CONVERSATIONS`
en `conversation_manager.py`). Esto significa que si Render redeploya o
reinicia el servicio, se pierden las conversaciones en curso (no las
citas — esas están seguras en Google Calendar). En la práctica el
paciente solo tendría que repetir su último mensaje.

Si más adelante esto se vuelve molesto, el siguiente paso natural es
mover `CONVERSATIONS` a una base de datos pequeña (SQLite con un volumen
persistente, o Redis). No lo necesitas para arrancar.

---

## 8. Costos totales estimados (bot en producción)

| Concepto | Costo mensual |
|---|---|
| Hosting Render Starter | $7 USD |
| API de Claude (Haiku, ~2,000 mensajes/mes) | ~$1-2 USD |
| Twilio WhatsApp (conversaciones de servicio) | variable, ~$3-10 USD según volumen |
| Google Calendar API | $0 (gratis en este volumen) |
| **Total aproximado** | **~$11-19 USD/mes** |

Empezando con Render Free para pruebas: solo pagas Claude + Twilio
(~$4-12 USD/mes).
