import asyncio
import json
import logging
import os
import websockets
import redis
from dotenv import load_dotenv

import logging
from utils.redis_utils import calcular_y_guardar_rsi, detectar_y_enviar_alertas

load_dotenv()
REDIS_URL = os.getenv("REDIS_URL")
redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)


logger = logging.getLogger("binance_ws")
logger.setLevel(logging.INFO)

async def handle_kline_processing(symbol, sid, user_id, kline_data, config, sio):
    k_data = kline_data.get("k", kline_data)
    symbol_upper = symbol.upper()
    backup_key = f"{symbol_upper}_last_failed_kline"
    close_price = round(float(k_data["c"]), 4)
    print(kline_data)
    # 1. Preparar datos y persistencia
    aligned_timestamp = (kline_data['E'] // 1000) * 1000 # Asumo que E está disponible o usas kline_data['t']
    optimized = {
        "timestamp": aligned_timestamp,
        "open": k_data["o"],
        "high": k_data["h"],
        "low": k_data["l"],
        "close": k_data["c"],
        "volume": k_data["v"],
        "number_of_trades": k_data["n"],
        "taker_buy_quote_asset_volume": k_data["Q"],
    }
    
    for attempt in range(3):
        try:
            # Operación de ZADD que puede ser lenta, mejor delegarla
            await asyncio.to_thread(
                redis_client.zadd, symbol_upper, {json.dumps(optimized): aligned_timestamp}
            )
            logger.info(f"📌 [{sid}] Guardado en Redis: {optimized}")
            
            # Limpiar backup y actualizar last_close (que ya se hizo arriba en el stream)
            await asyncio.to_thread(redis_client.delete, backup_key)
            
            # Cálculo de indicadores (potencialmente intensivo en CPU)
            indicators = await asyncio.to_thread(calcular_y_guardar_rsi, symbol, redis_client, sid)
            
            if indicators:
                await detectar_y_enviar_alertas(symbol, indicators, redis_client, sid)
                
            await sio.emit("operation_executed", config, to=sid)
            break
            
        except redis.exceptions.RedisError as e:
            logger.warning(f"[{sid}] ⚠️ Redis error (intento {attempt + 1}/3): {e}")
            if attempt == 0:
                # Guardar backup si falla el primer intento de escritura
                await asyncio.to_thread(redis_client.set, backup_key, json.dumps(optimized))
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"❌ Error en handle_kline_processing [{sid}]: {e}")