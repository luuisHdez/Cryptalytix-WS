from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, field_validator
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import redis
import os
import json
from dotenv import load_dotenv
from utils.auth_utils import verify_jwt_from_cookie

load_dotenv()

router = APIRouter()
redis_client = redis.StrictRedis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

class OperationConfig(BaseModel):
    symbol: str
    alert_up: str
    alert_down: str

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

@router.post("/operation-config")
def set_operation_config(config: OperationConfig,  request: Request):
    
    # Validar JWT desde la cookie HTTP-only
    user = verify_jwt_from_cookie(request)
    user_id = user.get("user_id")  # Por si quieres vincular operaciones a usuarios


    key = f"{config.symbol.upper()}_operation_{user_id}"
    redis_client.set(
        key,
        json.dumps({
            "symbol": config.symbol.upper(),
            "alert_up": "{:.4f}".format(float(config.alert_up)),
            "alert_down": "{:.4f}".format(float(config.alert_down)),
            "status": "inactive",
            "user_id": user_id  # útil si luego quieres filtrar por usuario
        })
    )
    return {"message": "Configuración guardada correctamente"}