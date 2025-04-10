import asyncio
import json
import logging
import os
from datetime import datetime
from utils.aws_gateway_client import trigger_apigateway_async
import math
from shared.socket_context import connected_users 
import redis
from dotenv import load_dotenv
import websockets

# Logger
logger = logging.getLogger("binance_ws")
logger.setLevel(logging.INFO)

# Redis
load_dotenv()
REDIS_URL = os.getenv("REDIS_URL")
redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)

# Almacena las tareas activas por cliente
client_tasks = {}

# Almacena tareas de evaluación activas
evaluation_tasks = {}

# Lógica de evaluación
async def evaluate_indicators(symbol: str, close_price: float, sid: str, sio):
    user_id = connected_users.get(sid)
    key = f"{symbol.upper()}_operation_{user_id}"
    config_json = redis_client.get(key)
    if not config_json:
        return

    config = json.loads(config_json)
    take_profit = float(config.get("take_profit", 0))
    stop_loss = float(config.get("stop_loss", 0))
    status = config.get("status", "inactive")
    entry_point = float(config.get("entry_point", 0))

    if status != "active":
        return

    if close_price >= take_profit:
        progress = ((take_profit - entry_point) / entry_point) * 100  # avance actual

        if progress >= 0.9:
            config["status"] = "closed"
            closed_at = datetime.utcnow().isoformat()
            config["closed_at"] = closed_at

            result_key = f"{symbol.upper()}_results"
            result_data = {
                "symbol": symbol.upper(),
                "entry_point": entry_point,
                "close_price": close_price,
                "operation": "TP",
                "activated_at": config.get("activated_at"),
                "closed_at": closed_at,
            }

            try:
                score = int(datetime.utcnow().timestamp())
                json_result = json.dumps(result_data)
                added = redis_client.zadd(result_key, {json_result: score})
                logger.info(f"[{sid}] 📌 Resultado agregado a ZADD ({result_key}): {added}")
            except Exception as e:
                logger.error(f"[{sid}] ⚠️ Error guardando en ZADD: {e}")

            try:
                redis_client.delete(key)
                logger.info(f"[{sid}] 🧹 SET eliminado: {key}")
            except Exception as e:
                logger.error(f"[{sid}] ⚠️ Error eliminando key {key}: {e}")

            logger.info(f"[{sid}] 🏁 Meta alcanzada (>{progress:.2f}%). Operación cerrada y registrada.")
            await sio.emit("operation_executed", config, to=sid)
            evaluation_tasks[sid].cancel()
            operation = "TP"
            await trigger_apigateway_async(config, operation)
            return



        # Actualizar take_profit y asignar nuevo take_benefit (-0.2% del nuevo TP)
        new_tp = round(take_profit * 1.002, 4)
        new_tb = round(take_profit * 0.998, 4)

        config["take_profit"] = new_tp
        config["take_benefit"] = new_tb
        redis_client.set(key, json.dumps(config))
        logger.info(f"[{sid}] ✅ take_profit alcanzado. Nuevo TP: {new_tp}, TB: {new_tb}")
        await sio.emit("operation_executed", config, to=sid)

    elif "take_benefit" in config and config["take_benefit"] is not None:
        if close_price <= float(config["take_benefit"]):
            config["status"] = "closed"
            closed_at = datetime.utcnow().isoformat()
            config["closed_at"] = closed_at

            result_key = f"{symbol.upper()}_results"
            result_data = {
                "symbol": symbol.upper(),
                "entry_point": entry_point,
                "close_price": close_price,
                "operation": "TB",
                "activated_at": config.get("activated_at"),
                "closed_at": closed_at,
            }

            try:
                score = int(datetime.utcnow().timestamp())
                json_result = json.dumps(result_data)
                added = redis_client.zadd(result_key, {json_result: score})
                logger.info(f"[{sid}] 📌 Resultado agregado a ZADD ({result_key}): {added}")
            except Exception as e:
                logger.error(f"[{sid}] ⚠️ Error guardando en ZADD: {e}")

            try:
                redis_client.delete(key)
                logger.info(f"[{sid}] 🧹 SET eliminado: {key}")
            except Exception as e:
                logger.error(f"[{sid}] ⚠️ Error eliminando key {key}: {e}")

            logger.info(f"[{sid}] ❌ take_benefit alcanzado. Operación cerrada y registrada.")
            operation = "TB"
            await trigger_apigateway_async(config, operation)
            await sio.emit("operation_executed", config, to=sid)

            if sid in evaluation_tasks:
                evaluation_tasks[sid].cancel()



    elif close_price <= stop_loss:
        config["status"] = "closed"
        closed_at = datetime.utcnow().isoformat()
        config["closed_at"] = closed_at

        result_key = f"{symbol.upper()}_results"
        result_data = {
            "symbol": symbol.upper(),
            "entry_point": entry_point,
            "close_price": close_price,
            "operation": "SL",
            "activated_at": config.get("activated_at"),
            "closed_at": closed_at,
        }

        try:
            score = int(datetime.utcnow().timestamp())
            json_result = json.dumps(result_data)
            added = redis_client.zadd(result_key, {json_result: score})
            logger.info(f"[{sid}] 📌 Resultado agregado a ZADD ({result_key}): {added}")
        except Exception as e:
            logger.error(f"[{sid}] ⚠️ Error guardando en ZADD: {e}")

        try:
            redis_client.delete(key)
            logger.info(f"[{sid}] 🧹 SET eliminado: {key}")
        except Exception as e:
            logger.error(f"[{sid}] ⚠️ Error eliminando key {key}: {e}")

        logger.info(f"[{sid}] ❌ stop_loss alcanzado. Operación cerrada y registrada.")
        operation = "SL"
        await trigger_apigateway_async(config, operation)
        await sio.emit("operation_executed", config, to=sid)

        if sid in evaluation_tasks:
            evaluation_tasks[sid].cancel()



# Alertas en tiempo real
async def check_alerts(symbol: str, close_price: float, sid: str, sio):
    logger.info(f"checking close price")
    user_id = connected_users.get(sid)
    key = f"{symbol.upper()}_operation_{user_id}"
    config_json = redis_client.get(key)
    if not config_json:
        return

    config = json.loads(config_json)
    alert_up = float(config.get("alert_up", 0))
    alert_down = float(config.get("alert_down", 0))

    if close_price >= alert_up:
        operation= "AU"
        await trigger_apigateway_async(config, operation)
        await sio.emit("alert_up_triggered", {"price": close_price}, to=sid)

    if close_price <= alert_down:
        operation= "AD"
        await trigger_apigateway_async(config, operation)
        await sio.emit("alert_down_triggered", {"price": close_price}, to=sid)

# Evaluación periódica cada 10 segundos
async def scheduled_evaluation(symbol: str, sid: str, sio):
    try:
        while True:
            user_id = connected_users.get(sid)
            key = f"{symbol.upper()}_operation_{user_id}"
            config_json = redis_client.get(key)
            if not config_json:
                await asyncio.sleep(10)
                continue

            config = json.loads(config_json)
            status = config.get("status", "inactive")
            last_close_str = redis_client.get(f"{symbol.upper()}_last_close")

            if status == "active" and last_close_str is not None:
                try:
                    close_price = float(last_close_str)
                    await evaluate_indicators(symbol.upper(), close_price, sid, sio)
                except Exception as e:
                    logger.error(f"[{sid}] ❗ Error convirtiendo last_close_str a float: {e}")
            elif status == "closed":
                break

            await asyncio.sleep(10)
    except Exception as e:
        logger.error(f"❌ Error en scheduled_evaluation [{sid}]: {e}")
        
# WebSocket principal
async def binance_stream(symbol: str, interval: str, sio, sid: str):
    print(connected_users[sid]," lalalala")
    binance_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@kline_{interval}"
    logger.info(f"📡 [{sid}] Conectando a Binance WS: {binance_url}")
    try:
        async with websockets.connect(binance_url) as ws:
            while True:
                raw_data = await ws.recv()
                data = json.loads(raw_data)
                
                await sio.emit("binance_data", data, to=sid)
                
                if 'k' in data:
                    kline = data['k']
                    close_price = round(float(kline["c"]), 4)
                    redis_client.set(f"{symbol.upper()}_last_close", close_price)
                    
                    await check_alerts(symbol.upper(), close_price, sid, sio)

                    # Activar operación si está inactiva y tocó entry_point
                    user_id = connected_users.get(sid)
                    key = f"{symbol.upper()}_operation_{user_id}"
                    config_json = redis_client.get(key)
                    if config_json:
                        config = json.loads(config_json)
                        entry_point = float(config.get("entry_point", 0))
                        if config.get("status") == "inactive" and math.isclose(close_price, entry_point, abs_tol=0.0002):
                            config["status"] = "active"

                            config["take_profit"] = round(entry_point * 1.006,4)
                            config["stop_loss"] = round(entry_point * 0.997, 4)

                            config["take_benefit"] = None
                            config["profit_progress"] = 0
                            config["activated_at"] = datetime.utcnow().isoformat()

                            for key_name in ["entry_point", "take_profit", "stop_loss", "alert_up", "alert_down", "profit_progress"]:
                                if key_name in config:
                                    config[key_name] = "{:.4f}".format(float(config[key_name]))
                            redis_client.set(key, json.dumps(config))
                            logger.info(f"[{sid}] 🚀 Activando operación: take_profit={config['take_profit']} stop_loss={config['stop_loss']}")
                            operation= "EP"
                            await trigger_apigateway_async(config, operation)

                            await sio.emit("operation_executed", config, to=sid)

                            # Iniciar evaluación periódica
                            print(sid, "este es el sid")
                            if sid not in evaluation_tasks:
                                task = asyncio.create_task(scheduled_evaluation(symbol.upper(), sid, sio))
                                evaluation_tasks[sid] = task

                if 'k' in data and data['k']['x']:
                    aligned_timestamp = (data['E'] // 1000) * 1000
                    optimized = {
                        "timestamp": aligned_timestamp,
                        "open": kline["o"],
                        "high": kline["h"],
                        "low": kline["l"],
                        "close": kline["c"],
                        "volume": kline["v"],
                        "number_of_trades": kline["n"],
                        "taker_buy_quote_asset_volume": kline["Q"],
                    }
                    redis_client.zadd(symbol.upper(), {json.dumps(optimized): aligned_timestamp})
                    logger.info(f"📌 [{sid}] Guardado en Redis: {optimized}")

    except Exception as e:
        logger.error(f"❌ [{sid}] Error en Binance WS: {e}")
