import os
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

# Carga .env
load_dotenv()

# Logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Variables de entorno
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "-1002311614150")
if not BOT_TOKEN or not CHAT_ID:
    logger.error("❌ Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el .env")

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# Sesión con reintentos automáticos
session = requests.Session()
retries = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST"]
)
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)


def send_telegram_message(text: str):
    """
    Envía un mensaje a Telegram usando la API de bots.
    - Retries automáticos en errores transitorios.
    - Timeout de 5s.
    - Loggea detalle de la respuesta en caso de fallo.
    """
    if not BOT_TOKEN or not CHAT_ID:
        return

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_notification": False
    }

    try:
        resp = session.post(API_URL, json=payload, timeout=5)
        resp.raise_for_status()
    except requests.HTTPError as e:
        # imprime código y cuerpo de la respuesta
        body = getattr(e.response, "text", "")
        logger.error(f"❌ HTTP {e.response.status_code} error al enviar Telegram: {body}")
    except requests.RequestException as e:
        logger.error(f"❌ Error de conexión al enviar Telegram: {e}")
    else:
        # comprueba que la API devolvió ok:true
        try:
            data = resp.json()
            if data.get("ok"):
                logger.info("✅ Mensaje enviado a Telegram")
            else:
                logger.error(f"❌ Telegram API devolvió error: {data}")
        except ValueError:
            logger.warning("⚠️ No se pudo parsear la respuesta de Telegram")
