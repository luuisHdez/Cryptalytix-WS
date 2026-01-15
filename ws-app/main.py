import os
import asyncio
from dotenv import load_dotenv
import socketio
import jwt
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware.cors import CORSMiddleware
from fastapi import FastAPI

# Imports de tu proyecto
from utils.redis_utils import evaluation_tasks
from shared.socket_context import sio, connected_users
from routes.historical_data import router as historical_router
from routes.historical_data_binance import router as historical_binance
from routes.operation_config import router as operation_config
from services import binance_ws
from services.telegram_bot import start_telegram_receiver, bot # Asegúrate que el nombre coincida
from ai.agents.hourly_analyst import agent_analysis

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")

# 1. Configuración de FastAPI
fastapi_app = FastAPI()
fastapi_app.include_router(historical_router)
fastapi_app.include_router(historical_binance)
fastapi_app.include_router(operation_config)

@fastapi_app.get("/")
async def root():
    return {"message": "Sistemas activos: FastAPI + Socket.IO + Telegram"}

@asynccontextmanager
async def lifespan(app: Starlette):
    # Arrancar Telegram
    telegram_task = asyncio.create_task(start_telegram_receiver())
    agent_task = asyncio.create_task(agent_analysis())
    yield  # La aplicación está funcionando
    
    # --- PROCESO DE CIERRE ---
    print("\n⏳ Apagando servicios...")
    
    # 1. Cancelamos la tarea de fondo
    telegram_task.cancel()
    agent_task.cancel()
    # 2. Cierre forzado de la conexión de red de Telegram
    # Esto rompe el "bloqueo" que impide cerrar el servidor
    await bot.session.close() 
    
    try:
        # Le damos solo 1 segundo para limpiar, si no, seguimos
        await asyncio.wait_for(telegram_task, timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
        
    print("✅ Servidor detenido limpiamente")

# 3. Creación de la App Starlette UNIFICADA
app = Starlette(
    lifespan=lifespan,
    routes=[
        Mount("/socket.io", app=socketio.ASGIApp(sio), name="socketio"),
        Mount("/", app=fastapi_app, name="fastapi"),
    ]
)

# 4. Middleware de CORS (Debe ir después de definir 'app')
# Agregamos tanto http como https para evitar el error de tu captura
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", 
        "https://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
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

    # 1. Eliminar usuario de la lista de conectados
    if sid in connected_users:
        print(f"  🔧 Eliminando {sid} de connected_users")
        del connected_users[sid]

    # 2. Cancelar el Stream de Binance (Tarea de recepción de datos)
    task_stream = binance_ws.client_tasks.get(sid)
    if task_stream:
        print(f"  🗑 Cancelando Stream de Binance para {sid}")
        task_stream.cancel()
        del binance_ws.client_tasks[sid]

    # 3. Cancelar la Tarea de Evaluación (El bucle de 10 segundos del Script 1)
    # Esto evita que el bot siga calculando ventas para alguien desconectado
    if sid in evaluation_tasks:
        print(f"  🛑 Deteniendo bucle de evaluación para {sid}")
        task_eval = evaluation_tasks.pop(sid) 
        task_eval.cancel()


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



from contextlib import asynccontextmanager # <--- Añade esto
from services.telegram_bot import start_telegram_receiver # <--- Importa tu bot

# ... tus otros imports ...

