import time
import json
import os
import redis
from logger import logger
from database import get_db_connection  # 🔥 Importar función de conexión
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv(dotenv_path=".env")

# Conexión a Redis
REDIS_URL = os.getenv("REDIS_URL")
redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)

def migrate_redis_to_postgres():
    """ 🔥 Migra datos de Redis a PostgreSQL y elimina los datos migrados después """
    while True:
        time.sleep(60)  # Ejecutar cada 1 minuto

        try:
            logger.info(" Iniciando migración de Redis a PostgreSQL...")
            conn = get_db_connection()  # 🔥 Se usa la conexión centralizada de `database.py`
            cursor = conn.cursor()

            for sorted_set_key in redis_client.keys("*"):
                redis_data = redis_client.zrangebyscore(sorted_set_key, "-inf", time.time() * 1000 - 300000)

                if redis_data:
                    for data in redis_data:
                        kline = json.loads(data)
                        cursor.execute("""
                        INSERT INTO candlesticks (symbol, open, high, low, close, volume, timestamp, number_of_trades, taker_buy_quote_asset_volume)
                        VALUES (%s, %s, %s, %s, %s, %s, CAST(%s AS BIGINT), %s, %s)
                        ON CONFLICT DO NOTHING;
                        """, (
                            sorted_set_key,
                            kline["open"],
                            kline["high"],
                            kline["low"],
                            kline["close"],
                            kline["volume"],
                            kline["timestamp"],
                            kline["number_of_trades"],
                            kline["taker_buy_quote_asset_volume"]
                        ))

                    conn.commit()
                    logger.info(f" Migrados {len(redis_data)} registros de {sorted_set_key} a PostgreSQL")

                    redis_client.zremrangebyscore(sorted_set_key, "-inf", time.time() * 1000 - 300000)

            cursor.close()
            conn.close()
            logger.info(" Migración completada.")

        except Exception as e:
            logger.error(f" Error en migración: {e}")
