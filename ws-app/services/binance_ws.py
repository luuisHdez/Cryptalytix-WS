import asyncio
import json
import logging
import os
import websockets
import redis
from dotenv import load_dotenv


from shared.socket_context import connected_users
from services.evaluator import evaluate_indicators
from services.alerts import check_alerts
from services.activation import check_activation
from utils.redis_utils import calcular_y_guardar_rsi, detectar_y_enviar_alertas
from utils.telegram_utils import send_telegram_message


# Logger
logger = logging.getLogger("binance_ws")
logger.setLevel(logging.INFO)

# Redis
load_dotenv()
REDIS_URL = os.getenv("REDIS_URL")
redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)

# Almacena las tareas activas por cliente
client_tasks = {}

# Almacena las tareas activas por cliente
evaluation_tasks = {}

# Evaluación periódica cada 10 segundos
async def scheduled_evaluation(symbol: str, sid: str, sio,user_id):
    try:
        while True:
            
            key = f"{symbol.upper()}_operation_{user_id}"
            #print(key)
            # Leer SIEMPRE de Redis
            try:
                config_json = redis_client.get(key)
                if not config_json:
                    
                    await asyncio.sleep(10)
                    continue
                config = json.loads(config_json)
            except redis.exceptions.RedisError as e:
                logger.warning(f"[{sid}] Redis error (get {key}): {e}")
                await asyncio.sleep(10)
                continue
            if not config.get("operate", False):
                break   # termina el bucle y la tarea
            try:
                last_close_str = redis_client.get(f"{symbol.upper()}_last_close")
            except redis.exceptions.RedisError as e:
                logger.warning(f"[{sid}] Redis error (get last_close): {e}")
                last_close_str = None
            
            
            if last_close_str is not None:
                try:
                    close_price = float(last_close_str)
                    await evaluate_indicators(symbol.upper(), close_price, sid, sio,user_id)
                except Exception as e:
                    logger.error(f"[{sid}] ❗ Error convirtiendo last_close_str a float: {e}")

            
            await asyncio.sleep(10)
    except Exception as e:
        logger.error(f"❌ Error en scheduled_evaluation [{sid}]: {e}")


# WebSocket principal
async def binance_stream(symbol: str, interval: str, sio, sid: str, user_id: int):
    
    binance_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@kline_{interval}"
    while True:
        logger.info(f"📡 [{sid}] Conectando a Binance WS: {binance_url}")
        try:
            async with websockets.connect(binance_url) as ws:
                while True:
                    raw_data = await ws.recv()
                    data = json.loads(raw_data)
                    backup_key = f"{symbol.upper()}_last_failed_kline"

                    # Intentar recuperar backup
                    try:
                        failed_raw = redis_client.get(backup_key)
                    except redis.exceptions.RedisError as e:
                        logger.warning(f"[{sid}] Redis error (get backup): {e}")
                        failed_raw = None

                    if failed_raw:
                        try:
                            failed_obj = json.loads(failed_raw)
                            redis_client.zadd(symbol.upper(), {failed_raw: failed_obj["timestamp"]})
                            redis_client.delete(backup_key)
                            logger.info(f"✅ [{sid}] Backup insertado (ts {failed_obj['timestamp']})")
                        except redis.exceptions.RedisError as e:
                            logger.warning(f"[{sid}] No se pudo volcar backup: {e}")

                    await sio.emit("binance_data", data, to=sid)
                    
                    if 'k' in data:
                        kline = data['k']
                        close_price = round(float(kline["c"]), 4)

                        try:
                            redis_client.set(f"{symbol.upper()}_last_close", close_price)
                        except redis.exceptions.RedisError as e:
                            logger.warning(f"[{sid}] Redis error (set last_close): {e}")
                        key = f"{symbol.upper()}_operation_{user_id}"

                    # 1) Lee SIEMPRE de Redis
                    try:
                        raw = redis_client.get(key)
                    except redis.exceptions.RedisError as e:
                        logger.warning(f"[{sid}] Redis error (get {key}): {e}")
                        continue

                    if not raw:
                        logger.warning(f"[{sid}] Configuración no encontrada en Redis")
                        continue

                    # 2) Parsea directamente
                    config = json.loads(raw)

                    # 3) Evaluaciones sin caché
                    if config.get("status") is True:
                        await check_alerts(symbol.upper(), close_price, sid, sio, redis_client, config)

                    if config.get("operate") is True and sid not in evaluation_tasks:
                        activated = await check_activation(symbol.upper(), close_price, sid, sio)
                        if activated:
                            task = asyncio.create_task(scheduled_evaluation(symbol.upper(), sid, sio, user_id))
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
                        for attempt in range(3):
                            try:
                                redis_client.zadd(symbol.upper(), {json.dumps(optimized): aligned_timestamp})
                                logger.info(f"📌 [{sid}] Guardado en Redis: {optimized}")
                                try:
                                    redis_client.delete(backup_key)
                                except redis.exceptions.RedisError as e:
                                    logger.warning(f"[{sid}] Redis error (delete backup): {e}")

                                indicators = calcular_y_guardar_rsi(symbol, redis_client, sid)
                                if not indicators:
                                    return  # o maneja el caso de datos insuficiente

                                # Luego puedes pasar todo el dict directamente a tu función de alertas:
                                await detectar_y_enviar_alertas(symbol, indicators, redis_client, sid)
                                await sio.emit("operation_executed", config, to=sid)
                                break
                            except redis.exceptions.RedisError as e:
                                logger.warning(f"[{sid}] ⚠️ Redis error (intento {attempt + 1}/3): {e}")
                                if attempt == 0:
                                    try:
                                        redis_client.set(backup_key, json.dumps(optimized))
                                    except redis.exceptions.RedisError as e2:
                                        logger.warning(f"[{sid}] Redis error (set backup): {e2}")
                                await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"❌ [{sid}] Error en Binance WS: {e}")
            await asyncio.sleep(5)

