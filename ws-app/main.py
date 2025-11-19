# main.py
import os
import asyncio
from dotenv import load_dotenv
import socketio
import jwt
from jwt.exceptions import PyJWTError

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware.cors import CORSMiddleware
from fastapi import FastAPI

from shared.socket_context import sio, connected_users
from routes.historical_data import router as historical_router
from routes.historical_data_binance import router as historical_binance
from routes.operation_config import router as operation_config
from services import binance_ws

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")

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
        Mount("/socket.io", app=socketio.ASGIApp(sio), name="socketio"),
        Mount("/", app=fastapi_app, name="fastapi"),
    ]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://localhost:5173"],
    allow_credentials=True,    # <— importante
    allow_methods=["*"],
    allow_headers=["*"],
)
# Eventos de conexión Socket.IO
@sio.event
async def connect(sid, environ, auth):
    
    jwt_token = None

    # 1. Intenta obtener token desde auth
    if auth and isinstance(auth, dict):
        jwt_token = auth.get("token")

    # 2. Si no, intenta desde las cookies
    if not jwt_token:
        cookies = environ.get("HTTP_COOKIE", "")
        for cookie in cookies.split(";"):
            name, _, value = cookie.strip().partition("=")
            if name == "jwt-auth":
                jwt_token = value
                break

    # 3. Si aún no hay token, rechaza
    if not jwt_token:
        print(f"⛔ Conexión rechazada: token ausente")
        raise ConnectionRefusedError("token missing")

    try:
        payload = jwt.decode(jwt_token, SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("user_id")
        print(f"✅ JWT válido. user_id: {user_id}")
        connected_users[sid] = user_id

    # Captura ambos errores (expirado e inválido) con PyJWTError
    except jwt.ExpiredSignatureError:
        print(f"⛔ JWT expirado")
        raise ConnectionRefusedError("token expired")
    except jwt.exceptions.PyJWTError as e: # ¡Importante! Usar la ruta completa
        print(f"⛔ JWT inválido: {e}")
        raise ConnectionRefusedError("token invalid")

    print(f" Cliente conectado: {sid}")



@sio.event
async def disconnect(sid):
    socket_obj = sio.eio.sockets.get(sid)
    print(f"  🔎 socket_obj.closed: {socket_obj.closed if socket_obj else 'no existe'}")

    if sid in connected_users:
        print("  🔧 Eliminando de connected_users")
        del connected_users[sid]

    task = binance_ws.client_tasks.get(sid)
    if task:
        print("  🗑 Cancelando tarea asociada")
        task.cancel()
        del binance_ws.client_tasks[sid]


@sio.event
async def subscribe(sid, data):
    symbol = data.get("symbol")
    interval = data.get("interval")
    print(f" [{sid}] Nueva suscripción a {symbol}/{interval}")

    # Cancelar stream anterior si existe
    if sid in binance_ws.client_tasks:
        binance_ws.client_tasks[sid].cancel()

    # Lanzar nueva tarea para este cliente
    user_id = connected_users[sid]
    task = asyncio.create_task(binance_ws.binance_stream(symbol, interval, sio, sid, user_id))
    binance_ws.client_tasks[sid] = task
    #asyncio.create_task(binance_ws.scheduled_evaluation(symbol, sid, sio))