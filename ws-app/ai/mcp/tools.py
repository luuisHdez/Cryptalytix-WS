import requests

BINANCE_API_URL = "https://api.binance.com/api/v3/klines"

import requests

BINANCE_API_URL = "https://api.binance.com/api/v3/klines"

def fetch_binance_klines(symbol: str, interval: str, limit: int):
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit
    }

    try:
        response = requests.get(BINANCE_API_URL, params=params)
        response.raise_for_status()
        result = response.json()

        klines = []
        cvd = 0.0

        for item in result:
            total_vol = float(item[5])
            taker_buy_vol = float(item[9])

            # Delta realista
            delta = (2 * taker_buy_vol) - total_vol
            cvd += delta

            klines.append({
                "t": int(item[0]),
                "o": float(item[1]),
                "h": float(item[2]),
                "l": float(item[3]),
                "c": float(item[4]),
                "v": total_vol,
                "delta": round(delta, 4),
                "cvd": round(cvd, 4)
            })

        return klines

    except Exception:
        return []


def get_market_technical_context(symbol: str):
    symbol_upper = symbol.upper()

    daily = fetch_binance_klines(symbol_upper, "1d", 10)
    hourly = fetch_binance_klines(symbol_upper, "1h", 20)

    m15 = fetch_binance_klines(symbol_upper, "15m", 32)

    if not m15:
        return {"error": "No data"}

    # Precio actual
    current_price = m15[-1]["c"]

    # --- RVOL 15m ---
    avg_vol = sum(k["v"] for k in m15[:-1]) / (len(m15) - 1)
    rvol = round(m15[-1]["v"] / avg_vol, 2) if avg_vol > 0 else 0

    # --- CVD 15m (bloque completo) ---
    cvd_start = m15[0]["cvd"]
    cvd_end = m15[-1]["cvd"]
    cvd_delta = round(cvd_end - cvd_start, 4)

    price_start = m15[0]["c"]
    price_end = m15[-1]["c"]
    price_delta = round(price_end - price_start, 4)

    # Lectura estructural
    if price_delta < 0 and cvd_delta > 0:
        flow_state = "Absorción (compras no mueven precio)"
    elif price_delta > 0 and cvd_delta < 0:
        flow_state = "Distribución (ventas absorben subidas)"
    elif price_delta > 0 and cvd_delta > 0:
        flow_state = "Continuación alcista"
    elif price_delta < 0 and cvd_delta < 0:
        flow_state = "Continuación bajista"
    else:
        flow_state = "Equilibrio / rango"

    def summarize(klines):
        return [f"C:{k['c']}, V:{k['v']}" for k in klines]
    
    return {
        "current_price": current_price,
        "rvol_15m": rvol,
        "order_flow_15m": {
            "cvd_delta": cvd_delta,
            "price_delta": price_delta,
            "state": flow_state
        },
        "data_macro": {
            "d10": summarize(daily),
            "h20": summarize(hourly),
            "m15": summarize(m15)
        }
    }
