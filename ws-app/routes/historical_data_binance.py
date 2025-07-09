import requests
import json
from datetime import datetime, timedelta,timezone
from fastapi import APIRouter, HTTPException, Query
from database import get_db_connection

router = APIRouter()

BINANCE_API_URL = "https://api.binance.com/api/v3/klines"

@router.get("/update-binance-data/")
async def update_binance_data(
    symbol: str = Query(..., description="Símbolo de la criptomoneda, e.g., BTCUSDT"),
    start_time: int = Query(..., description="Inicio del rango en timestamp (ms)"),
    end_time: int = Query(..., description="Fin del rango en timestamp (ms)")
):
    """
    📌 Endpoint que obtiene datos históricos de Binance en intervalos de 1 minuto
    dentro de un rango de fechas definido por el usuario y los almacena en PostgreSQL.
    """
    if start_time >= end_time:
        raise HTTPException(status_code=400, detail="start_time debe ser menor que end_time.")

    params = {
        "symbol": symbol.upper(),
        "interval": "1m",  # 🔥 Intervalo fijo de 1 minuto
        "startTime": start_time,
        "endTime": end_time,
        "limit": 1000
    }

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        values = []
        while start_time < end_time:
            response = requests.get(BINANCE_API_URL, params=params)
            response.raise_for_status()
            result = response.json()

            if not result:
                break

            for item in result:
                values.append({
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "timestamp": int(item[0]),
                    "volume": float(item[5]),
                    "number_of_trades": int(item[8]),
                    "taker_buy_quote_asset_volume": float(item[10])
                })

            # 🔥 Avanzar al siguiente lote de datos
            start_time = result[-1][0] + 60000  # Agregar 1 minuto
            params["startTime"] = start_time

        # 🔥 Insertar en PostgreSQL evitando duplicados
        for kline in values:
            cursor.execute("""
                INSERT INTO candlesticks (symbol, open, high, low, close, volume, timestamp, number_of_trades, taker_buy_quote_asset_volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, timestamp) DO NOTHING;
            """, (
                symbol.upper(),
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
        cursor.close()
        conn.close()

        return {
            "symbol": symbol.upper(),
            "interval": "1m",
            "total_inserted": len(values),
            "message": "Datos de Binance insertados en la base de datos."
        }

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener datos de Binance: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}")


async def sync_recent_candles(symbol: str, interval: str = "1m"):
    conn = get_db_connection()
    cursor = conn.cursor()

        

    # Definir rango desde ahora (UTC +6 horas) hacia atrás 24h
    now_utc = datetime.now(timezone.utc)
    end_time = int((now_utc + timedelta(hours=6)).timestamp() * 1000)
    start_time = end_time - 24 * 60 * 60 * 1000  # 24 horas en milisegundos


    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "startTime": start_time,
        "endTime": end_time,
        "limit": 1000
    }

    values = []
    while start_time < end_time:
        response = requests.get("https://api.binance.com/api/v3/klines", params=params)
        response.raise_for_status()
        result = response.json()

        if not result:
            break

        for item in result:
            values.append({
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "timestamp": int(item[0]),
                "volume": float(item[5]),
                "number_of_trades": int(item[8]),
                "taker_buy_quote_asset_volume": float(item[10])
            })

        start_time = result[-1][0] + 60000
        params["startTime"] = start_time

    for kline in values:
        cursor.execute("""
            INSERT INTO candlesticks (symbol, open, high, low, close, volume, timestamp, number_of_trades, taker_buy_quote_asset_volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, timestamp) DO NOTHING;
        """, (
            symbol.upper(),
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
    cursor.close()
    conn.close()
