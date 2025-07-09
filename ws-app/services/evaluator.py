import json
import logging
from datetime import datetime
import math
from utils.aws_gateway_client import trigger_apigateway_async
from shared.socket_context import connected_users
from utils.redis_utils import redis_client

logger = logging.getLogger("binance_ws")

evaluation_tasks = {}

async def evaluate_indicators(symbol: str, close_price: float, sid: str, sio):
    user_id = connected_users.get(sid)
    key = f"{symbol.upper()}_operation_{user_id}"
    config_json = redis_client.get(key)
    if not config_json:
        return

    config = json.loads(config_json)
    take_profit = float(config.get("take_profit", 0))
    stop_loss = float(config.get("stop_loss", 0))
    entry_point = float(config.get("entry_point", 0))
    status = config.get("status", "inactive")
    # --- Generar entry_point si no existe y condiciones son favorables ---
   
    if status != "active":
        return
    
    raw = redis_client.zrevrange(symbol.upper(), 0, 0)
    if not raw:
        return

    try:
        vela = json.loads(raw[0])
        rsi      = float(vela.get("rsi"))
        ema6     = float(vela.get("ema6"))
        bb_upper = float(vela.get("bb_upper"))
        high     = float(vela.get("high"))
        close    = float(vela.get("close"))
    except (TypeError, ValueError):
        return  # Datos incompletos

    # --- Cierre por pico de RSI ---
    max_rsi_key = f"{symbol.upper()}_max_rsi_{user_id}"

    if rsi >= 90:
        prev_max_rsi = redis_client.get(max_rsi_key)
        if prev_max_rsi is None or rsi > float(prev_max_rsi):
            redis_client.set(max_rsi_key, round(rsi, 4))
        elif rsi < float(prev_max_rsi) and high > bb_upper * 0.9995 and close > ema6:
            await _close_operation(symbol, close_price, config, key, sid, sio, "RSI_PEAK")
            redis_client.delete(max_rsi_key)
            return
    else:
        redis_client.delete(max_rsi_key)


    if close_price >= take_profit:

        new_tp = round(take_profit * 1.003, 4)
        new_tb = round(take_profit * 0.999, 4)

        config["take_profit"] = new_tp
        config["take_benefit"] = new_tb
        redis_client.set(key, json.dumps(config))
        logger.info(f"[{sid}] ✅ TP alcanzado. Nuevo TP: {new_tp}, TB: {new_tb}")
        await sio.emit("operation_executed", config, to=sid)

    elif config.get("take_benefit") is not None and close_price <= float(config["take_benefit"]):
        await _close_operation(symbol, close_price, config, key, sid, sio, "TB")

    elif close_price <= stop_loss:
        await _close_operation(symbol, close_price, config, key, sid, sio, "SL")


async def _close_operation(symbol, close_price, config, key, sid, sio, operation_type):
    # Asegurarse de usar dict
    if isinstance(config, str):
        config = json.loads(config)

    # 1. Marcar la operación como cerrada
    config["status"] = "closed"
    closed_at = datetime.utcnow().isoformat()
    config["closed_at"] = closed_at

    # 2. Datos a registrar en el histórico
    result_data = {
        "symbol":       symbol.upper(),
        "entry_point":  float(config.get("entry_point", 0)),
        "close_price":  close_price,
        "operation":    operation_type,
        "activated_at": config.get("activated_at"),
        "closed_at":    closed_at,
    }

    # 3. Guardar resultado + reiniciar config en Redis
    await _store_result(symbol, result_data, key, config, sid)


    # 4. Limpiar campos que ya no sirven y dejar solo las alertas activas
    for field in ("entry_point", "take_profit", "stop_loss", "take_benefit"):
        config.pop(field, None)
    config["status"] = "inactive"

    # 5. Notificar al frontend
    await sio.emit("operation_executed", config, to=sid)

    # 6. Cancelar tarea de evaluación, si existe
    if sid in evaluation_tasks:
        evaluation_tasks[sid].cancel()

    # 7. Enviar notificación externa
    await trigger_apigateway_async(config, operation_type)


async def _store_result(symbol, result_data, key, config, sid, redis_key_suffix="_results"):
    result_key = f"{symbol.upper()}{redis_key_suffix}"
    try:
        score = int(datetime.utcnow().timestamp())
        json_result = json.dumps(result_data)
        redis_client.zadd(result_key, {json_result: score})
    except Exception as e:
        logger.error(f"[{sid}] ⚠️ Error ZADD: {e}")

    try:
        # conservar alert_up y alert_down para la próxima oportunidad
        new_config = {
            "alert_up": config.get("alert_up"),
            "alert_down": config.get("alert_down"),
            "status": "inactive"
        }
        redis_client.set(key, json.dumps(new_config))
        logger.info(f"[{sid}] 🔁 Config reiniciada con alertas activas.")
    except Exception as e:
        logger.error(f"[{sid}] ⚠️ Error al guardar nueva config: {e}")