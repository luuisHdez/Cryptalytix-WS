from fastapi import APIRouter, HTTPException
from database import get_db_connection
import pandas as pd
from datetime import timedelta

router = APIRouter()

VALID_INTERVALS = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "1H",
    "1d": "1D"
}

@router.get("/historical-data/{symbol}/{interval}")
async def get_historical_data(symbol: str, interval: str, before: int = None, limit: int = 500):
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail="Intervalo no válido. Usa: 1m, 5m, 15m, 1h, 1d.")

    try:
        conn = get_db_connection()

        # Convertir 'before' (UNIX en segundos) a milisegundos para la consulta
        sql_before = before * 1000 if before else None

        if sql_before:
            query = """
                SELECT timestamp, open, high, low, close, volume, number_of_trades
                FROM public.candlesticks
                WHERE symbol = %s AND timestamp < %s
                ORDER BY timestamp DESC
                LIMIT %s
            """
            df = pd.read_sql_query(query, conn, params=(symbol.upper(), sql_before, limit))
        else:
            query = """
                SELECT timestamp, open, high, low, close, volume, number_of_trades
                FROM public.candlesticks
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """
            df = pd.read_sql_query(query, conn, params=(symbol.upper(), limit))

        conn.close()

        if df.empty:
            raise HTTPException(status_code=404, detail="No hay datos disponibles para este símbolo e intervalo.")

        df = df.sort_values(by='timestamp')  # volver a orden ascendente
        df['ts'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('ts', inplace=True)

        # Resampleo con pandas
        resampled = df.resample(VALID_INTERVALS[interval]).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
            'number_of_trades': 'sum'
        }).dropna()

        resampled.reset_index(inplace=True)

        # Ajuste de zona horaria manual: UTC-6
        offset_seconds = -6 * 3600
        resampled['ts_adjusted'] = resampled['ts'] + timedelta(seconds=offset_seconds)

        response = [
            {
                "time": int(row['ts_adjusted'].timestamp()),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": float(row['volume']),
                "trades": int(row['number_of_trades'])
            }
            for _, row in resampled.iterrows()
        ]

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener datos: {str(e)}")

from routes.historical_data_binance import sync_recent_candles
from fastapi import Request

@router.get("/update-then-fetch/{symbol}/{interval}")
async def update_then_fetch(symbol: str, interval: str, request: Request):
    # 1. Actualiza desde Binance → PostgreSQL
    await sync_recent_candles(symbol, interval)

    # 2. Luego carga desde la base de datos
    return await get_historical_data(symbol, interval, before=request.query_params.get("before"))