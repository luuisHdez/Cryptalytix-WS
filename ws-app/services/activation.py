# services/activation.py

import json
import logging
from datetime import datetime
from shared.socket_context import connected_users
from utils.aws_gateway_client import trigger_apigateway_async
import redis
import os
from dotenv import load_dotenv

# Cargar configuración y conectar a Redis
load_dotenv()
REDIS_URL = os.getenv("REDIS_URL")
redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)

logger = logging.getLogger("activation")

async def check_activation(symbol: str, close_price: float, sid: str, sio):
    user_id = connected_users.get(sid)
    key = f"{symbol.upper()}_operation_{user_id}"
    config_json = redis_client.get(key)
    if not config_json:
        return False

    config = json.loads(config_json)

    # — Obtener la última vela para leer indicadores
    raw = redis_client.zrevrange(symbol.upper(), 0, 0)
    if not raw:
        return False

    vela = json.loads(raw[0])
    rsi = vela.get("rsi")
    if rsi is None:
        return False  # RSI no calculado todavía

    try:
        vela = json.loads(raw[0])
        rsi      = float(vela.get("rsi"))
        ema6     = float(vela.get("ema6"))
        bb_lower = float(vela.get("bb_lower"))
        low      = float(vela.get("low"))
        close    = float(vela.get("close"))
    except (TypeError, ValueError):
        return False  # datos incompletos o mal formateados

    
    # ✅ Condición compuesta de activación
    try:
        vela = json.loads(raw[0])
        rsi      = float(vela.get("rsi"))
        ema6     = float(vela.get("ema6"))
        bb_lower = float(vela.get("bb_lower"))
        low      = float(vela.get("low"))
        close    = float(vela.get("close"))
    except (TypeError, ValueError):
        return False

    if config.get("status") != "inactive":
        return False

    min_rsi_key = f"{symbol.upper()}_min_rsi_{user_id}"

    if rsi <= 20:
        prev_min_rsi = redis_client.get(min_rsi_key)
        if prev_min_rsi is None or rsi < float(prev_min_rsi):
            redis_client.set(min_rsi_key, round(rsi, 4))
        return False  # esperamos confirmación
    else:
        prev_min_rsi = redis_client.get(min_rsi_key)
        if prev_min_rsi is not None and rsi > float(prev_min_rsi):
            bb_range = round(bb_lower * 1.0005, 4)
            if low <= bb_range and close < ema6:
                entry = close_price
                config["entry_point"]     = entry
                config["take_profit"]     = round(entry * 1.005, 4)
                config["stop_loss"]       = round(entry * 0.996, 4)
                config["status"]          = "active"
                config["profit_progress"] = 0
                config["activated_at"]    = datetime.utcnow().isoformat()

                for field in ["entry_point", "take_profit", "stop_loss", "profit_progress"]:
                    config[field] = "{:.4f}".format(float(config[field]))

                redis_client.set(key, json.dumps(config))
                redis_client.delete(min_rsi_key)
                await trigger_apigateway_async(config, "EP")
                await sio.emit("operation_executed", config, to=sid)
                logger.info(f"[{sid}] ✅ Activación confirmada: RSI={rsi}, LOW <= BB_LOW({low} <= {bb_range}), CLOSE < EMA6({close} < {ema6})")
                return True
        redis_client.delete(min_rsi_key)

    return False