import json
import logging
from datetime import datetime
from services.binance_api import close_market_order
from utils.telegram_utils import send_telegram_message
from shared.socket_context import connected_users
from utils.redis_utils import redis_client

logger = logging.getLogger("binance_ws")

evaluation_tasks = {}

async def evaluate_indicators(symbol: str, close_price: float, sid: str, sio,user_id):
    
    key = f"{symbol.upper()}_operation_{user_id}"

    # 1) Leer SIEMPRE desde Redis
    try:
        raw = redis_client.get(key)
        if not raw:
            return
        config = json.loads(raw)
    except redis_client.exceptions.RedisError as e:
        logger.warning(f"[{sid}] Redis error (get {key}): {e}")
        return

    entry_point = float(config.get("entry_point", 0))
    take_profit = float(config.get("take_profit",  0))
    stop_loss   = float(config.get("stop_loss",    0))
    tb_str      = config.get("take_benefit")
    take_benefit= float(tb_str) if tb_str is not None else None
    
    # 2) Cierres definitivos, en orden de urgencia:
    # 2.1 Stop‑loss
    if stop_loss and close_price <= stop_loss:
        close_result = close_market_order(symbol)
        if close_result.get("status") == "FILLED":
            await _close_operation(symbol, close_price, config, key, sid, sio, "SL", close_result)
        else:
            logger.warning(f"[{sid}] Falló SL: {close_result}")
        return

    # 2.2 Take‑benefit
    if take_benefit is not None and close_price <= take_benefit:
        close_result = close_market_order(symbol)
        if close_result.get("status") == "FILLED":
            await _close_operation(symbol, close_price, config, key, sid, sio, "TB", close_result)
        else:
            logger.warning(f"[{sid}] Falló TB: {close_result}")
        return

    # 2.3 Take‑profit final
    if entry_point > 0 and close_price >= take_profit:
        profit_progress = round((close_price - entry_point) / entry_point, 4)
        if profit_progress >= 2:
            close_result = close_market_order(symbol)
            if close_result.get("status") == "FILLED":
                await _close_operation(symbol, close_price, config, key, sid, sio, "TP", close_result)
            else:
                logger.warning(f"[{sid}] Falló TP final: {close_result}")
            return
        # 2.4 Ajuste dinámico de TP/TB
        new_tp = round(take_profit * 1.005, 4)
        new_tb = round(take_profit * 0.996, 4)
        config.update({"take_profit": new_tp, "take_benefit": new_tb})
        redis_client.set(key, json.dumps(config))
        logger.info(f"[{sid}] TP dinámico: TP={new_tp}, TB={new_tb}")
        await sio.emit("operation_executed", config, to=sid)
        return

    # 3) Si no encaja en ningún cierre, no hacer nada




async def _close_operation(symbol, close_price, config, key, sid, sio, operation_type,close_result):
    # Asegurarse de usar dict
    if isinstance(config, str):
        config = json.loads(config)

    fills = close_result.get("fills", [])

    formatted_fills = [
            {
                "price": f["price"],
                "qty": f["qty"],
                "commission": f["commission"],
                "commissionAsset": f["commissionAsset"],
                "tradeId": f["tradeId"]
            }
            for f in fills
        ]

    result_data = {
            "symbol": symbol.upper(),
            "operation": operation_type,
            "side": close_result.get("side"),
            "price_avg": "{:.4f}".format(close_price),
            "amount": "{:.4f}".format(float(close_result.get("executedQty"))),
            "total_usdt": "{:.4f}".format(float(close_result.get("cummulativeQuoteQty"))),
            "commission": "{:.4f}".format(sum(float(f["commission"]) for f in fills)),
            "commission_asset": fills[0].get("commissionAsset") if fills else "USDT",
            "executed_at": datetime.utcnow().isoformat(),
            "fills": formatted_fills
        }
    
    # 4. Limpiar campos que ya no sirven y dejar solo las alertas activas
    for field in ("entry_point", "take_profit", "stop_loss", "take_benefit"):
        config.pop(field, None)

    # 3. Guardar resultado + reiniciar config en Redis
    await _store_result(symbol, result_data, key, config, sid)

    # 5. Notificar al frontend
    await sio.emit("operation_executed", config, to=sid)

    # 6. Cancelar tarea de evaluación, si existe
    # 6. Cancelar tarea de evaluación, si existe
    if sid in evaluation_tasks:
        task = evaluation_tasks.pop(sid)
        task.cancel()


    # 7. Enviar notificación externa
    plain = (
            f"🔔 {symbol.upper()} operación cerrada ({operation_type})\n"
            f"Side: {result_data['side']}\n"
            f"Precio de cierre: {result_data['price_avg']} USDT\n"
            f"Cantidad vendida: {result_data['amount']} {symbol.upper()}\n"
            f"Total USDT: {result_data['total_usdt']}\n"
            f"Comisión: {result_data['commission']} {result_data['commission_asset']}\n"
            f"Hora ejecución: {result_data['executed_at']}"
        )
    send_telegram_message(plain)


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
            "status": config.get("status"),
            "operate": False
        }
        redis_client.set(key, json.dumps(new_config))

        logger.info(f"[{sid}] 🔁 Config reiniciada con alertas activas.")
    except Exception as e:
        logger.error(f"[{sid}] ⚠️ Error al guardar nueva config: {e}")