# services/alerts.py
import json
import logging
import time
import asyncio
from utils.aws_gateway_client import trigger_apigateway_async
from shared.socket_context import connected_users
from utils.telegram_utils import send_telegram_message

logger = logging.getLogger("alerts")

MAX_ALERTS   = 1           # máximo de alertas permitidas
WINDOW_SECS  = 100          # ventada deslizante
COOLDOWN_SECS = 10         # intervalo mínimo entre alertas

def _should_alert(redis_client, key_prefix: str) -> bool:
    """
    Control de frecuencia:
    • Máx. 3 alertas en 30 s
    • Mín. 10 s entre alertas
    Claves usadas:
      {prefix}:ts   → timestamp última alerta
      {prefix}:cnt  → contador en la ventana
    """
    now = int(time.time())
    ts_key  = f"{key_prefix}:ts"
    cnt_key = f"{key_prefix}:cnt"

    pipe = redis_client.pipeline()
    pipe.get(ts_key)
    pipe.get(cnt_key)
    last_ts, count = pipe.execute()

    # Normaliza valores
    last_ts = int(last_ts) if last_ts is not None else 0
    count   = int(count)   if count   is not None else 0

    # Demasiado pronto
    if now - last_ts < COOLDOWN_SECS:
        return False

    # Ventana de 30 s expirada → reinicia contador
    if now - last_ts >= WINDOW_SECS:
        count = 0

    # Límite de 3 alertas
    if count >= MAX_ALERTS:
        return False

    # Actualiza timestamp y contador (TTL auto-limpieza)
    pipe.set(ts_key, now, ex=WINDOW_SECS)
    pipe.set(cnt_key, count + 1, ex=WINDOW_SECS)
    pipe.execute()
    return True

async def check_alerts(symbol: str, close_price: float, sid: str, sio, redis_client, config):
   
    user_id   = connected_users.get(sid)
    alert_up   = float(config.get("alert_up",   0))
    alert_down = float(config.get("alert_down", 0))

    # -------- Alert UP --------
    if alert_up and close_price >= alert_up:
        prefix = f"{symbol.upper()}_{user_id}_AU"
        if await asyncio.to_thread(_should_alert, redis_client, prefix):
            send_telegram_message(
                f"🚨 Alerta UP de {symbol.upper()}\n"
                f"Precio actual: {close_price}\n"
                f"Umbral UP:     {alert_up}\n"
                f"Identificador: {prefix}"
            )
            logger.info(f"[{sid}] 🚨 Alert UP enviada ({close_price} ≥ {alert_up})")

    # -------- Alert DOWN --------
    if alert_down and close_price <= alert_down:
        prefix = f"{symbol.upper()}_{user_id}_AD"
        if await asyncio.to_thread(_should_alert, redis_client, prefix):
            send_telegram_message(
                f"🚨 Alerta DOWN de {symbol.upper()}\n"
                f"Precio actual: {close_price}\n"
                f"Umbral DOWN:   {alert_down}\n"
                f"Identificador: {prefix}"
            )
            await sio.emit("alert_down_triggered", {"price": close_price}, to=sid)
            logger.info(f"[{sid}] 🚨 Alert DOWN enviada ({close_price} ≤ {alert_down})")
