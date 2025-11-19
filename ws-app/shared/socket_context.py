# shared/socket_context.py
import socketio
from typing import Dict
# Creamos la única instancia de AsyncServer para todo el proyecto
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins=["https://localhost:5173"],
    cors_credentials=True,
    transports=["websocket"],      # Solo websocket
    allow_upgrades=True,           # No polling
    ping_timeout=60,
    ping_interval=25,
    cors_allow_credentials=True,   # Permite enviar cookies
)

# Mapa global de SID → user_id
connected_users: Dict[str, int] = {}
config_cache: Dict[str, dict] = {}