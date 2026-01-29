from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, field_validator
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import redis
import os
import json
import asyncio
from dotenv import load_dotenv
from utils.auth_utils import verify_jwt_from_cookie
from fastapi import APIRouter, Request, Depends, HTTPException
from datetime import datetime
from utils.auth_utils import verify_jwt_from_cookie
from services.binance_api import place_market_order
from shared.socket_context import connected_users, sio
from services.evaluator import evaluate_indicators, evaluation_tasks
from utils.telegram_utils import send_telegram_message


load_dotenv()

router = APIRouter()
redis_client = redis.StrictRedis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

class OperationConfig(BaseModel):
    symbol: str
    alert_up: str
    alert_down: str
    active_operations: bool
    active_alerts: bool

    @field_validator('alert_up', 'alert_down')
    @classmethod
    def validate_decimal_precision(cls, v, info):
        try:
            value = Decimal(str(v)).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            if abs(value.as_tuple().exponent) != 4:
                raise ValueError(f"{info.name} debe tener exactamente 4 decimales")
            return value
        except InvalidOperation:
            raise ValueError(f"{info.name} no es un número válido con 4 decimales")

from fastapi import HTTPException, status

@router.post("/operation-config")
def set_operation_config(config: OperationConfig, request: Request):
    try:
        user = verify_jwt_from_cookie(request)
        user_id = user.get("user_id")
        key = f"{config.symbol.upper()}_operation_{user_id}"

        # Paso 1: Intenta obtener configuración previa
        previous_config = {}
        try:
            existing = redis_client.get(key)
            if existing:
                previous_config = json.loads(existing)
        except redis.exceptions.RedisError as e:
            raise HTTPException(status_code=500, detail=f"Redis error (get): {str(e)}")

        # Paso 2: Solo actualiza campos proporcionados, conserva los demás
        operation_data = {
            **previous_config,  # merge con anterior
            "symbol": config.symbol.upper(),
            "alert_up": "{:.4f}".format(float(config.alert_up)),
            "alert_down": "{:.4f}".format(float(config.alert_down)),
            "status": config.active_alerts,
            "operate": config.active_operations,
            "user_id": user_id,
        }

        # Paso 3: Guarda en Redis
        try:
            redis_client.set(key, json.dumps(operation_data))
        except redis.exceptions.RedisError as e:
            raise HTTPException(status_code=500, detail=f"Redis error (set): {str(e)}")

        return {"message": "Configuración actualizada correctamente"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error inesperado: {str(e)}")


class StopConfig(BaseModel):
    symbol: str

@router.post("/stop-operation")
def stop_operation(config: StopConfig, request: Request):
    try:
        user = verify_jwt_from_cookie(request)
        user_id = user.get("user_id")

        key = f"{config.symbol.upper()}_operation_{user_id}"

        try:
            config_json = redis_client.get(key)
        except redis.exceptions.RedisError as e:
            raise HTTPException(status_code=500, detail=f"Error de Redis: {str(e)}")

        if not config_json:
            raise HTTPException(status_code=404, detail="No se encontró configuración activa para este símbolo")

        try:
            data = json.loads(config_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Configuración corrupta en Redis")


        data['status'] = False
        data['operate'] = False

        try:
            redis_client.set(key, json.dumps(data))
        except redis.exceptions.RedisError as e:
            raise HTTPException(status_code=500, detail=f"No se pudo guardar la configuración: {str(e)}")

        return {"message": "Operación detenida correctamente"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error inesperado: {str(e)}")

from services.binance_api import place_market_order, close_market_order



class SymbolRequest(BaseModel):
    symbol: str
    active_operations: bool = False
    active_alerts: bool = False

@router.post("/buy-order")
@router.post("/buy-order")
async def execute_buy_order(data: SymbolRequest, request: Request):
    try:
        # 1. Obtener datos de usuario y símbolo primero
        user = verify_jwt_from_cookie(request)
        user_id = user.get("user_id")
        symbol = data.symbol.upper()

        # 2. VALIDAR CONFIGURACIÓN EN REDIS ANTES DE COMPRAR
        # Si esto falla, el flujo se detiene aquí y no se gasta dinero
        key = f"{symbol}_operation_{user_id}"
        try:
            redis_data = redis_client.get(key)
            if not redis_data:
                raise HTTPException(status_code=404, detail="No hay configuración activa para este símbolo")
            config = json.loads(redis_data)
        except redis.exceptions.RedisError as e:
            raise HTTPException(status_code=500, detail=f"Error de Redis: {str(e)}")

        # 3. EJECUTAR ORDEN EN BINANCE
        # Ahora estamos seguros de que tenemos dónde guardar el resultado
        result = await place_market_order(symbol)
        
        if result.get("status") != "FILLED":
            raise HTTPException(status_code=400, detail="Orden no fue completada.")

        fills = result.get("fills", [])
        if not fills:
            raise HTTPException(status_code=500, detail="Orden sin fills, no se puede continuar.")

        # 4. CÁLCULOS Y ACTUALIZACIÓN DE LÓGICA
        executed_qty = float(result.get("executedQty"))
        cummulative_quote = float(result.get("cummulativeQuoteQty"))
        entry = round(cummulative_quote / executed_qty, 4)

        config["entry_point"]     = entry
        config["take_profit"]     = round(entry * 1.010, 4)
        config["stop_loss"]       = round(entry * 0.995, 4)
        
        # Ajuste para evitar error de Pydantic/Atributos usando .get() de la config de Redis
        config["status"]          = config.get("active_alerts", True) 
        config["operate"]         = config.get("active_operations", True)           

        config["profit_progress"] = 0
        config["activated_at"]    = datetime.utcnow().isoformat()
        config["binance"] = {
            "orderId": result.get("orderId"),
            "side": result.get("side"),
            "executedQty": result.get("executedQty"),
            "cummulativeQuoteQty": result.get("cummulativeQuoteQty"),
            "transactTime": result.get("transactTime"),
            "commission": sum(float(f["commission"]) for f in result.get("fills", [])),
        }

        # Formatear campos numéricos
        for field in ["entry_point", "take_profit", "stop_loss", "profit_progress"]:
            config[field] = "{:.4f}".format(float(config[field]))

        # Guardar actualización en Redis
        redis_client.set(key, json.dumps(config))

        # 5. HISTÓRICO Y NOTIFICACIONES (Sin cambios en lógica)
        formatted_fills = [
            {
                "price": f["price"],
                "qty": f["qty"],
                "commission": f["commission"],
                "commissionAsset": f["commissionAsset"],
                "tradeId": f["tradeId"]
            }
            for f in fills
        ]

        result_key = f"{symbol}_results"
        entry_result = {
            "symbol": symbol,
            "operation": "EP",
            "side": result.get("side"),
            "price_avg": "{:.4f}".format(entry),
            "amount": "{:.4f}".format(float(result.get("executedQty"))),
            "total_usdt": "{:.4f}".format(float(result.get("cummulativeQuoteQty"))),
            "commission": "{:.4f}".format(sum(float(f["commission"]) for f in fills)),
            "commission_asset": fills[0].get("commissionAsset") if fills else "USDT",
            "executed_at": config.get("activated_at"),
            "fills": formatted_fills
        }

        score = int(datetime.utcnow().timestamp())
        redis_client.zadd(result_key, {json.dumps(entry_result): score})
        
        plain = (
            f"🟢 {symbol} compra manual ejecutada\n"
            f"🛒 Cantidad: {entry_result['amount']} {symbol}\n"
            f"💰 Precio promedio: {entry_result['price_avg']} USDT\n"
            f"💸 Total USDT: {entry_result['total_usdt']}\n"
            f"🧾 Comisión: {entry_result['commission']} {entry_result['commission_asset']}\n"
            f"⏱️ Ejecutado a las: {entry_result['executed_at']}"
        )
        send_telegram_message(plain)

        # 6. TAREAS ASÍNCRONAS
        sid = next((s for s, u in connected_users.items() if u == user_id), None)
        if sid and sid not in evaluation_tasks:
            task = asyncio.create_task(evaluate_indicators(symbol, entry, sid, sio, user_id))
            evaluation_tasks[sid] = task

        return {"message": "✅ Orden ejecutada manualmente y configuración actualizada", "details": config}

    except HTTPException as e:
        raise e
    except Exception as e:
        import traceback
        print("❌ Error inesperado en /buy-order:")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Error inesperado en el servidor.")


@router.post("/sell-order")
async def execute_sell_order(data: SymbolRequest, request: Request):

    try:
        user = verify_jwt_from_cookie(request)
        user_id = user.get("user_id")
        symbol = data.symbol.upper()

        sid = next((s for s, u in connected_users.items() if u == user_id), None)
        if not sid:
            raise HTTPException(status_code=403, detail="Socket isnt connected to this user")
        
        key = f"{symbol}_operation_{user_id}"
        config_str = redis_client.get(key)
        if not config_str:
            raise HTTPException(status_code=404, detail=f"No existe configuración para {symbol}")

        try:
            config = json.loads(config_str)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Configuración corrupta en Redis")


        config["status"]          = data.active_alerts
        config["operate"]         = data.active_operations
        
        close_operation = await close_market_order(symbol)
        if close_operation.get('status') != "FILLED":
            raise HTTPException(status_code=400, detail="Order not completed")
        
        from services.evaluator import _close_operation
        executed_qty = float(close_operation.get("executedQty"))
        cummulative_quote = float(close_operation.get("cummulativeQuoteQty"))
        exit_price = round(cummulative_quote / executed_qty, 4)
        await _close_operation(symbol,exit_price,config, key, sid, sio, "CO", close_operation )
        
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail= f"Error inesperado: { str(e)}")