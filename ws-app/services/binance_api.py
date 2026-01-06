import os
import time
import hmac
import hashlib
import requests
from dotenv import load_dotenv
import httpx

load_dotenv()

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")
IS_DEV = os.getenv("ENV", "development").lower() != "production"
PERCENTAGE_TO_USE = float(os.getenv("OPERATION_PERCENT", 5))  # Solo para producción

BASE_URL = "https://api.binance.com"
HEADERS = {
    "X-MBX-APIKEY": BINANCE_API_KEY
}

import decimal, math

def adjust_quantity(symbol: str, balance: float) -> float:
    try:
        url = f"{BASE_URL}/api/v3/exchangeInfo"
        resp = requests.get(url, params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()

        for f in data["symbols"][0]["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                precision = abs(decimal.Decimal(str(step_size)).as_tuple().exponent)
                adjusted_qty = round(math.floor(balance / step_size) * step_size, precision)
                print(f"✅ Cantidad ajustada para {symbol}: {adjusted_qty}")
                return adjusted_qty

        raise ValueError("No se encontró LOT_SIZE para el símbolo.")

    except Exception as e:
        print(f"❌ Error al ajustar quantity: {e}")
        return 0.0


def get_timestamp():
    return int(time.time() * 1000)


def sign_payload(payload: dict) -> dict:
    try:
        query_string = "&".join([f"{k}={v}" for k, v in payload.items()])
        signature = hmac.new(BINANCE_SECRET.encode(), query_string.encode(), hashlib.sha256).hexdigest()
        payload["signature"] = signature
        return payload
    except Exception as e:
        print(f"❌ Error al firmar el payload: {e}")
        raise


def get_balance(asset: str) -> float:
    try:
        url = f"{BASE_URL}/api/v3/account"
        params = {
            "timestamp": get_timestamp()
        }
        signed_params = sign_payload(params)

        response = requests.get(url, headers=HEADERS, params=signed_params)
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            raise ValueError(f"❌ Error al decodificar respuesta JSON: {response.text}")

        for item in data.get("balances", []):
            if item["asset"] == asset:
                balance = float(item["free"])
                print(f"📊 Saldo detectado de {asset}: {balance}")
                return balance

        print(f" No se encontró {asset} en la cuenta.")
        return 0.0

    except Exception as e:
        print(f"❌ Error al obtener balance de USDT: {e}")
        return 0.0



def place_market_order(symbol: str):
    try:
        if IS_DEV:
            quantity = 2  # Compra exacta de 1 unidad
        else:
            usdt_balance = get_balance("USDT")
            print(f"📊 Saldo USDT detectado: {usdt_balance}")

            if usdt_balance <= 0:
                raise ValueError("Balance USDT insuficiente.")

            usdt_to_use = (PERCENTAGE_TO_USE / 100) * usdt_balance
            print(f"➡️ Se usará: {usdt_to_use} USDT para comprar {symbol}")
            # Obtener precio actual para calcular cantidad
            price_url = f"{BASE_URL}/api/v3/ticker/price"
            price_resp = requests.get(price_url, params={"symbol": symbol})
            price_resp.raise_for_status()

            price_data = price_resp.json()
            current_price = float(price_data.get("price", 0))
            if current_price <= 0:
                raise ValueError("Precio inválido obtenido.")

            raw_qty = usdt_to_use / current_price
            quantity = adjust_quantity(symbol, raw_qty)


        url = f"{BASE_URL}/api/v3/order"
        params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quantity": quantity,
            "timestamp": get_timestamp()
        }

        signed_params = sign_payload(params)
        response = requests.post(url, headers=HEADERS, params=signed_params)
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            raise ValueError(f"❌ Error al decodificar respuesta JSON: {response.text}")

        if "status" not in data or data.get("status") != "FILLED":
            raise ValueError(f"❌ Orden no completada: {data}")

        print("✅ Orden ejecutada:", data)
        return data

    except Exception as e:
        print(f"❌ Error en place_market_order: {e}")
        return {
            "status": "ERROR",
            "message": str(e)
        }



async def close_market_order(symbol: str):
    try:
        asset = symbol.replace("USDT", "")
        
        # Mantenemos tu lógica de entorno de desarrollo/producción
        if IS_DEV:
            quantity = 5  
        else:
            # Si get_balance y adjust_quantity son síncronas, se quedan igual.
            # Si fueran asíncronas, habría que agregar 'await'.
            raw_balance = get_balance(asset)
            quantity = adjust_quantity(symbol, raw_balance)
            
            if quantity <= 0:
                msg = f"⚠️ No hay balance disponible de {asset} para vender."
                print(msg)
                return {"status": "ERROR", "message": msg}

        url = f"{BASE_URL}/api/v3/order"
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": quantity,
            "timestamp": get_timestamp()
        }

        signed_params = sign_payload(params)

        # Usamos httpx.AsyncClient para NO bloquear el flujo de los otros scripts
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=HEADERS, params=signed_params, timeout=10.0)
            response.raise_for_status()
            data = response.json()

        # Validación de salida igual a la original
        if "status" not in data or data.get("status") != "FILLED":
            raise ValueError(f"❌ Orden de venta no completada: {data}")

        return data

    except Exception as e:
        print(f"❌ Error en close_market_order: {e}")
        # Retorno idéntico al original para no romper la lógica de 'evaluate_indicators'
        return {
            "status": "ERROR",
            "message": str(e)
        }