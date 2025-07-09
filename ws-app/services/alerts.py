# services/alerts.py

import json
import logging
from utils.aws_gateway_client import trigger_apigateway_async
from shared.socket_context import connected_users

logger = logging.getLogger("alerts")

async def check_alerts(symbol: str, close_price: float, sid: str, sio, redis_client):
    user_id = connected_users.get(sid)
    key = f"{symbol.upper()}_operation_{user_id}"
    config_json = redis_client.get(key)
    if not config_json:
        return

    config = json.loads(config_json)
    alert_up = float(config.get("alert_up", 0))
    alert_down = float(config.get("alert_down", 0))

    if close_price >= alert_up:
        operation = "AU"
        await trigger_apigateway_async(config, operation)
        await sio.emit("alert_up_triggered", {"price": close_price}, to=sid)

    if close_price <= alert_down:
        operation = "AD"
        await trigger_apigateway_async(config, operation)
        await sio.emit("alert_down_triggered", {"price": close_price}, to=sid)
