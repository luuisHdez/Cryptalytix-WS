import socketio
from starlette.routing import Mount
from starlette.applications import Starlette
from fastapi import FastAPI
import asyncio
from services import binance_ws
from routes.historical_data import router as historical_router
from routes.historical_data_binance import router as historical_binance
from routes.operation_config import router as operation_config
import os
from dotenv import load_dotenv
from shared.socket_context import connected_users

load_dotenv()


SECRET_KEY = os.getenv("SECRET_KEY")

# Socket.IO Server
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins=["https://localhost:5173"]  # 🔒 Ajusta a tu frontend
)

sio_app = socketio.ASGIApp(sio)

# FastAPI App
fastapi_app = FastAPI()
fastapi_app.include_router(historical_router)
fastapi_app.include_router(historical_binance)
fastapi_app.include_router(operation_config)


@fastapi_app.get("/")
async def root():
    return {"message": "FastAPI con Socket.IO dinámico"}

# App combinada con Starlette
app = Starlette(
    routes=[
        Mount("/socket.io", app=sio_app),
        Mount("/", app=fastapi_app),
    ]
)

import jwt  # Asegúrate de tener PyJWT instalado
from jwt import InvalidTokenError
# Eventos de conexión Socket.IO
@sio.event
async def connect(sid, environ):
    cookies = environ.get("HTTP_COOKIE", "")
    jwt_token = None
    for cookie in cookies.split(";"):
        name, _, value = cookie.strip().partition("=")
        if name == "jwt-auth":
            jwt_token = value
            break

    if not jwt_token:
        print(f"⛔ Conexión rechazada: token ausente")
        raise ConnectionRefusedError("token missing")

    try:
        payload = jwt.decode(jwt_token, SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("user_id")
        print(f"✅ JWT válido. user_id: {user_id}")

        connected_users[sid] = user_id

    except jwt.ExpiredSignatureError:
        print(f"⛔ JWT expirado")
        raise ConnectionRefusedError("token expired")
    except InvalidTokenError as e:
        print(f"⛔ JWT inválido: {e}")
        raise ConnectionRefusedError("token invalid")

    print(f"🟢 Cliente conectado: {sid}")


@sio.event
async def disconnect(sid):
    print(f"🔴 Cliente desconectado: {sid}")
    if sid in connected_users:
        del connected_users[sid]
    task = binance_ws.client_tasks.get(sid)

    if task:
        task.cancel()
        del binance_ws.client_tasks[sid]

@sio.event
async def subscribe(sid, data):
    symbol = data.get("symbol")
    interval = data.get("interval")
    print(f"📥 [{sid}] Nueva suscripción a {symbol}/{interval}")

    # Cancelar stream anterior si existe
    if sid in binance_ws.client_tasks:
        binance_ws.client_tasks[sid].cancel()

    # Lanzar nueva tarea para este cliente
    task = asyncio.create_task(binance_ws.binance_stream(symbol, interval, sio, sid))
    binance_ws.client_tasks[sid] = task
    asyncio.create_task(binance_ws.scheduled_evaluation(symbol, sid, sio))

from starlette.middleware.cors import CORSMiddleware

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
