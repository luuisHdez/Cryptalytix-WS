import json
from datetime import datetime
from logger import logger
import pandas as pd
import os
import redis
from dotenv import load_dotenv
from database import get_db_connection
from shared.socket_context import connected_users, config_cache

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


from typing import List

def calcular_rsi_exacto(all_closes: List[float], period: int = 14) -> float:

    series = pd.Series(all_closes)
    delta  = series.diff()

    # Separar ganancias y pérdidas
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    # 1) Seed con media simple de los primeros `period` deltas
    avg_gain = gain.iloc[1:period+1].mean()
    avg_loss = loss.iloc[1:period+1].mean()

    # 2) Wilder smoothing hacia adelante
    for i in range(period+1, len(series)):
        g = gain.iloc[i]
        l = loss.iloc[i]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    # 3) RSI final
    rs  = avg_gain / avg_loss if avg_loss != 0 else float("inf")
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 4)



def calcular_y_guardar_rsi(symbol: str, redis_client, sid: str = "", period: int = 14):
    
    try:
        # 1) Leer todo el histórico de velas con scores (timestamp)
        entries_scores = redis_client.zrange(symbol.upper(), 0, -1, withscores=True)
        if len(entries_scores) < period + 1:
            return None

        # Extraer precios de cierre y timestamp de la última vela
        closes = [float(json.loads(member)['close']) for member, _ in entries_scores]
        latest_member, latest_score = entries_scores[-1]

        # 2) RSI exacto Wilder
        latest_rsi = calcular_rsi_exacto(closes, period)

        # 3) EMAs con Pandas EWM
        series = pd.Series(closes)
        latest_ema10 = latest_ema50 = latest_ema150 = None
        if len(closes) >= 10:
            latest_ema10 = round(float(series.ewm(span=10, adjust=False).mean().iloc[-1]), 4)
        if len(closes) >= 50:
            latest_ema50 = round(float(series.ewm(span=50, adjust=False).mean().iloc[-1]), 4)
        if len(closes) >= 150:
            latest_ema150 = round(float(series.ewm(span=150, adjust=False).mean().iloc[-1]), 4)

        # 4) Bollinger Bands (20 periodos, 2 desviaciones)
        if len(closes) >= 20:
            window = pd.Series(closes[-20:])
            sma = window.mean()
            std = window.std()
            bb_upper = round(sma + 2 * std, 4)
            bb_lower = round(sma - 2 * std, 4)
            bb_basis = round(sma, 4)
        else:
            bb_upper = bb_lower = bb_basis = None

        # 5) Actualizar la última vela en Redis
        try:
            vela = json.loads(latest_member)
            vela.update({
                "rsi": latest_rsi,
                "ema10": latest_ema10,
                "ema50": latest_ema50,
                "ema150": latest_ema150,
                "bb_upper": bb_upper,
                "bb_lower": bb_lower,
                "bb_basis": bb_basis,
            })
            # Reemplazar la vela antigua
            redis_client.zremrangebyscore(symbol.upper(), latest_score, latest_score)
            redis_client.zadd(symbol.upper(), {json.dumps(vela): latest_score})
        except redis.exceptions.RedisError as e:
            logger.warning(f"[{sid}] ⚠️ Error actualizando vela en Redis: {e}")

        # 6) Precio de cierre más reciente
        close_price = round(closes[-1], 4)

        return {
            "rsi": latest_rsi,
            "ema10": latest_ema10,
            "ema50": latest_ema50,
            "ema150": latest_ema150,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_basis": bb_basis,
            "close": close_price
        }

    except Exception as e:
        logger.error(f"❌ Error calculando/guardando indicadores para {symbol}: {e}")
        return None



from utils.telegram_utils import send_telegram_message  # asegúrate que esté importado

async def detectar_y_enviar_alertas(symbol, indicators, redis_client, sid):
    user_id    = connected_users.get(sid)
    key        = f"{symbol.upper()}_operation_{user_id}"

    # cargar config siempre de Redis
    try:
        raw = redis_client.get(key)
        if not raw:
            return
        config = json.loads(raw)
    except redis.exceptions.RedisError as e:
        logger.warning(f"[{sid}] Redis error (get {key}): {e}")
        return

    if not config.get("status", False):
        return

    # extraer indicadores
    rsi         = indicators["rsi"]
    bb_lower    = indicators["bb_lower"]
    close_price = indicators["close"]
    ema50       = indicators["ema50"]

    # flags previos
    key_rsi_flag = f"{symbol}_rsi_alerted_{user_id}"
    key_bb_flag  = f"{symbol}_bblow_alerted_{user_id}"
    was_rsi      = redis_client.get(key_rsi_flag) == "1"
    was_bb       = redis_client.get(key_bb_flag)  == "1"

    # condiciones
    cond_rsi      = (rsi < 25)
    cond_bb       = (close_price < bb_lower)
    crossed_up    = redis_client.get(f"{symbol}_ema50_crossed_up_{user_id}")   == "1"
    crossed_down  = redis_client.get(f"{symbol}_ema50_crossed_down_{user_id}") == "1"
    cond_ema_down = close_price < ema50 and crossed_up
    cond_ema_up   = close_price > ema50 and crossed_down

    # — Eliminar el resumen de vela cerrada —
    # (no se envía send_telegram_message para cada vela)

    # actualizar flags RSI
    if cond_rsi and not was_rsi:
        send_telegram_message(f"🟢 RSI alert for {symbol.upper()}: {rsi} (<25)")
        redis_client.set(key_rsi_flag, "1")
    elif not cond_rsi and was_rsi:
        redis_client.delete(key_rsi_flag)

    # actualizar flags Bollinger
    if cond_bb and not was_bb:
        send_telegram_message(f"🟢 Bollinger Lower alert for {symbol.upper()}: {close_price} < {bb_lower}")
        redis_client.set(key_bb_flag, "1")
    elif not cond_bb and was_bb:
        redis_client.delete(key_bb_flag)

    # inicializar cruce EMA al primer run
    key_up   = f"{symbol}_ema50_crossed_up_{user_id}"
    key_down = f"{symbol}_ema50_crossed_down_{user_id}"
    if redis_client.get(key_up) is None and redis_client.get(key_down) is None:
        redis_client.set(key_up if close_price > ema50 else key_down, "1")

    # limpiar cruces tras dispararlos
    if cond_ema_down:
        redis_client.set(key_down, "1")
        redis_client.delete(key_up)
        send_telegram_message(f"🔻 EMA50 down cross for {symbol.upper()}: Close {close_price} < EMA50 {ema50}")
    if cond_ema_up:
        redis_client.set(key_up, "1")
        redis_client.delete(key_down)
        send_telegram_message(f"🔺 EMA50 up cross for {symbol.upper()}: Close {close_price} > EMA50 {ema50}")




def sync_recent_candles_redis(symbol: str, limit: int = 200) -> None:
    """
    Si Redis no tiene al menos `limit` velas para `symbol`,
    consulta PostgreSQL y precarga las últimas `limit` en Redis.
    """
    key = symbol.upper()
    if redis_client.zcard(key) >= limit:
        return  # Ya hay suficientes datos

    # --- 1. Leer de PostgreSQL ---
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT timestamp, open, high, low, close, volume,
               number_of_trades, taker_buy_quote_asset_volume
        FROM candlesticks
        WHERE symbol = %s
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        (key, limit),
    )
    rows = cursor.fetchall()
    print(rows)
    cursor.close()
    conn.close()

    if not rows:
        logger.warning(f"⚠️ No hay datos en PostgreSQL para {key}")
        return

    # --- 2. Cargar en Redis en orden cronológico ---
    pipe = redis_client.pipeline()
    for row in reversed(rows):
        vela = {
            "timestamp": row[0],
            "open": str(row[1]),
            "high": str(row[2]),
            "low": str(row[3]),
            "close": str(row[4]),
            "volume": str(row[5]),
            "number_of_trades": row[6],
            "taker_buy_quote_asset_volume": str(row[7]),
        }
        pipe.zadd(key, {json.dumps(vela): row[0]})
    pipe.execute()
    logger.info(f"✅ Redis precargado con {len(rows)} velas para {key}")
