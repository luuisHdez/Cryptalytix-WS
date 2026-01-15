import requests
import logging

logger = logging.getLogger("mcp_tools")

BINANCE_API_URL = "https://api.binance.com/api/v3/klines"

def fetch_binance_klines(symbol: str, interval: str, limit: int):
    """
    Obtiene velas directamente de Binance siguiendo la lógica de tus scripts.
    """
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit
    }
    try:
        response = requests.get(BINANCE_API_URL, params=params)
        response.raise_for_status()
        result = response.json()
        
        # Mapeamos siguiendo tus índices: 0:ms, 1:o, 2:h, 3:l, 4:c, 5:v
        return [{
            "t": int(item[0]),
            "o": float(item[1]),
            "h": float(item[2]),
            "l": float(item[3]),
            "c": float(item[4]),
            "v": float(item[5])
        } for item in result]
    except Exception as e:
        logger.error(f"Error consultando Binance ({interval}): {e}")
        return []

def get_market_technical_context(symbol: str):
    symbol_upper = symbol.upper()
    daily = fetch_binance_klines(symbol_upper, "1d", 10)
    hourly = fetch_binance_klines(symbol_upper, "1h", 20)
    m15 = fetch_binance_klines(symbol_upper, "15m", 20)

    if not m15:
        return {"error": "No data"}

    # Reducimos los datos a solo Cierre y Volumen para ahorrar espacio
    def summarize(klines):
        return [f"C:{k['c']}, V:{k['v']}" for k in klines]

    current_price = m15[-1]["c"]
    avg_vol = sum(k["v"] for k in m15) / len(m15)
    rvol = round(m15[-1]["v"] / avg_vol, 2) if avg_vol > 0 else 0

    return {
        "current_price": current_price,
        "rvol": rvol,
        "data_macro": {
            "d10": summarize(daily),
            "h20": summarize(hourly),
            "m20": summarize(m15)
        }
    }
