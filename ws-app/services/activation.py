# services/activation.py

import json
import logging
from datetime import datetime
from shared.socket_context import connected_users
from utils.aws_gateway_client import trigger_apigateway_async
from services.binance_api import place_market_order
import redis
import os
from dotenv import load_dotenv
from utils.telegram_utils import send_telegram_message 

# Cargar configuración y conectar a Redis
load_dotenv()
REDIS_URL = os.getenv("REDIS_URL")
redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)

logger = logging.getLogger("activation")

async def check_activation(symbol: str, close_price: float, sid: str, sio):
    user_id = connected_users.get(sid)
    key = f"{symbol.upper()}_operation_{user_id}"

    # — después de este bloque —
    try:
        config_json = redis_client.get(key)
        if not config_json:
            return False
        config = json.loads(config_json)
    except redis.exceptions.RedisError as e:
        logger.warning(f"[{sid}] Redis error (get {key}): {e}")
        return False

    # 🔥 Si ya existe un objeto ‘binance’ en la config, detenemos aquí
    if config.get("binance"):
        logger.info(f"[{sid}] ⚡ Operación en curso: {config['binance']}")
        print("operacion abierta sell/buy")
        return True

    if config.get("operate") is not True:
        return False

    # — Obtener la última vela para leer indicadores
    raw = redis_client.zrevrange(symbol.upper(), 0, 0)
    if not raw:
        return False

    vela = json.loads(raw[0])
    rsi = vela.get("rsi")
    ema10 = vela.get("ema10")
    ema50 = vela.get("ema50")
    ema150 = vela.get("ema150")

    if None in (rsi, ema10, ema50, ema150):
        return False  # Indicadores aún no calculados

    try:
        vela = json.loads(raw[0])
        rsi      = round(float(vela.get("rsi")), 4)
        ema10    = round(float(vela.get("ema10")), 4)
        ema50    = round(float(vela.get("ema50")), 4)
        ema150   = round(float(vela.get("ema150")), 4)
        close    = round(float(vela.get("close")), 4)
    except (TypeError, ValueError):
        return False  # datos incompletos o mal formateados

    

    min_rsi_key = f"{symbol.upper()}_min_rsi_{user_id}"

    # Notificación si RSI cae a 20 o menos
    if rsi <= 20:
        prev_min_rsi = redis_client.get(min_rsi_key)
        if prev_min_rsi is None or rsi < float(prev_min_rsi):
            redis_client.set(min_rsi_key, round(rsi, 4))
            # solo notificación, no condición de entrada
        return False  # no operamos aún

    # Condición base para operar
    if close > ema50:
        if (
            close > ema10 and
            ema10 > ema50 
            and ema10 > ema150
            and close < rsi 
        ):
                        # --- Intentar comprar en Binance ---
            try:
                order_response = {}
                #place_market_order(symbol)
                if order_response.get("status") != "FILLED":
                    logger.warning(f"[{sid}]  Orden no completada en Binance: {order_response}")
                    return False

                # Calcular precio promedio de compra real desde los fills
                fills = order_response.get("fills", [])
                if not fills:
                    logger.error(f"[{sid}]  Orden sin fills, no se puede continuar.")
                    return False

                executed_qty =  float(order_response.get("executedQty"))
                cummulative_quote = float(order_response.get("cummulativeQuoteQty"))
                entry = round(cummulative_quote / executed_qty, 4)

                # Guardar metadatos de la orden de compra
                config["binance"] = {
                    "orderId": order_response.get("orderId"),
                    "side": order_response.get("side"),
                    "executedQty": order_response.get("executedQty"),
                    "cummulativeQuoteQty": order_response.get("cummulativeQuoteQty"),
                    "transactTime": order_response.get("transactTime"),
                    "commission": sum(float(f["commission"]) for f in order_response.get("fills", [])),
                }

            except Exception as e:
                logger.error(f"[{sid}] Error al ejecutar orden en Binance: {e}")
                return False

            # Si todo fue exitoso, usar el precio real de entrada
            config["entry_point"]     = entry
            config["take_profit"]     = round(entry * 1.005, 4)
            config["stop_loss"]       = round(entry * 0.997, 4)
            config["profit_progress"] = 0
            config["activated_at"]    = datetime.utcnow().isoformat()

            for field in ["entry_point", "take_profit", "stop_loss", "profit_progress"]:
                config[field] = "{:.4f}".format(float(config[field]))

            redis_client.set(key, json.dumps(config))
            redis_client.delete(min_rsi_key)

            # ✅ Guardar en histórico
            result_key = f"{symbol.upper()}_results"
            entry_result = {
                "symbol": symbol.upper(),
                "entry_point": entry,
                "operation": "EP",
                "activated_at": config.get("activated_at"),
                "buy_order": config.get("binance"),
            }
            score = int(datetime.utcnow().timestamp())
            redis_client.zadd(result_key, {json.dumps(entry_result): score})
            buy_order = config["binance"]
            executed_qty = buy_order.get("executedQty")
            price = entry
            commission = buy_order.get("commission", 0)

            message = (
                f"🚀 {symbol.upper()} operación ejecutada\n"
                f"🛒 Cantidad comprada: {executed_qty} {symbol.upper()}\n"
                f"💰 Precio de entrada: {price} USDT\n"
                f"💸 Comisión: {commission} (BNB)\n"
                f"📊 TP: {config['take_profit']} / SL: {config['stop_loss']}\n"
                f"⏱️ Hora: {config['activated_at']}"
            )
            send_telegram_message(message)
            await sio.emit("operation_executed", config, to=sid)
            logger.info(
                f"[{sid}] Activación — CLOSE {close} > EMA150 {ema150}, "
                f"EMA10 {ema10} > EMA50 {ema50} y EMA10 > EMA150"
            )
            return True
