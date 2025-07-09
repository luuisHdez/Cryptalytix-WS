import json
from datetime import datetime
from logger import logger
import pandas as pd
import os
import redis
from dotenv import load_dotenv
import talib as ta

load_dotenv()
REDIS_URL = os.getenv("REDIS_URL")
redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)


# Guarda resultado en ZADD
def save_result_to_redis(redis_client, symbol: str, result_data: dict):
    try:
        result_key = f"{symbol.upper()}_results"
        score = int(datetime.utcnow().timestamp())
        json_result = json.dumps(result_data)
        added = redis_client.zadd(result_key, {json_result: score})
        logger.info(f"📌 Resultado agregado a ZADD ({result_key}): {added}")
    except Exception as e:
        logger.error(f"⚠️ Error guardando en ZADD: {e}")

# Elimina la configuración de operación
def delete_operation(redis_client, key: str, sid: str = ""):
    try:
        redis_client.delete(key)
        logger.info(f"[{sid}] 🧹 SET eliminado: {key}")
    except Exception as e:
        logger.error(f"[{sid}] ⚠️ Error eliminando key {key}: {e}")

# Extrae user_id desde el sid conectado
def get_user_id(connected_users: dict, sid: str) -> str:
    return connected_users.get(sid)


# ✅ Calcular y guardar RSI
def calcular_y_guardar_rsi(symbol: str, redis_client, sid: str = "", period: int = 6):
    try:
        # Traer suficientes cierres (BB necesita 10)
        required_closes = 10
        raw = redis_client.zrevrange(symbol.upper(), 0, required_closes, withscores=True)
        if len(raw) < required_closes + 1:
            return None

        entries, scores = zip(*raw[::-1])  # cronológico
        closes = [float(json.loads(m)['close']) for m in entries]

        # ========= RSI WILDER (suavizado incremental) =========
        key_gain = f"{symbol.upper()}_avg_gain"
        key_loss = f"{symbol.upper()}_avg_loss"
        prev_gain = redis_client.get(key_gain)
        prev_loss = redis_client.get(key_loss)

        if prev_gain is None or prev_loss is None:
            deltas = [closes[i+1] - closes[i] for i in range(period)]
            gains  = [d if d > 0 else 0 for d in deltas]
            losses = [abs(d) if d < 0 else 0 for d in deltas]
            avg_gain = sum(gains) / period
            avg_loss = sum(losses) / period
        else:
            avg_gain = float(prev_gain)
            avg_loss = float(prev_loss)

        delta = closes[-1] - closes[-2]
        current_gain = delta if delta > 0 else 0
        current_loss = abs(delta) if delta < 0 else 0
        avg_gain = (avg_gain * (period - 1) + current_gain) / period
        avg_loss = (avg_loss * (period - 1) + current_loss) / period

        redis_client.set(key_gain, avg_gain)
        redis_client.set(key_loss, avg_loss)

        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        rsi = 100 - (100 / (1 + rs))
        latest_rsi = round(rsi, 4)

        # ========= EMA 6 =========
        all_raw = redis_client.zrange(symbol.upper(), 0, -1)  # cronológico completo
        all_closes = [float(json.loads(e)['close']) for e in all_raw]
        if len(all_closes) >= 6:
            ema_series = pd.Series(all_closes).ewm(span=6, adjust=False).mean()
            latest_ema = round(float(ema_series.iloc[-1]), 4)
        else:
            latest_ema = None  # aún no disponible

        # ========= Bollinger Bands 10 x 1.5 =========
        if len(all_closes) >= 10:
            bb_series = pd.Series(all_closes[-10:])
            sma = bb_series.mean()
            std = bb_series.std()
            bb_upper = round(sma + (1.5 * std), 4)
            bb_lower = round(sma - (1.5 * std), 4)
            bb_basis = round(sma, 4)
        else:
            bb_upper = bb_lower = bb_basis = None

        # ========= Actualizar vela más reciente en Redis =========
        try:
            latest_member, latest_score = raw[0]
            vela = json.loads(latest_member)
            vela["rsi"] = latest_rsi
            vela["ema6"] = latest_ema
            vela["bb_upper"] = bb_upper
            vela["bb_lower"] = bb_lower
            vela["bb_basis"] = bb_basis

            redis_client.zremrangebyscore(symbol.upper(), latest_score, latest_score)
            redis_client.zadd(symbol.upper(), {json.dumps(vela): latest_score})
        except redis.exceptions.RedisError as e:
            logger.warning(f"[{sid}] ⚠️ Error actualizando vela en Redis: {e}")

        return latest_rsi

    except Exception as e:
        logger.error(f"❌ Error calculando/guardando indicadores para {symbol}: {e}")
        return None

