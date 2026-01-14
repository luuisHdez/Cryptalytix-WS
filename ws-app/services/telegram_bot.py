import os
import asyncio
from aiogram import Bot, Dispatcher, types

# Variables de entorno
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Inicialización de objetos
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Handler unificado: Captura mensajes privados, de grupo y menciones
@dp.message()
async def handle_input(message: types.Message):
    # Log básico en consola para cualquier mensaje que detecte el bot
    print(message)
    print(f"\n[SISTEMA] Mensaje detectado en chat ID {message.chat.id} ({message.chat.type})")
    print("="*30)
    print(f"🔔 ¡MENSAJE RECIBIDO!")
    print(f"De: {message.from_user.full_name}")
    print(f"Texto: {message.text}")
    print("="*30 + "\n")

# Función principal de arranque
async def start_telegram_receiver():
    try:
        # 1. Limpieza de webhooks y actualizaciones pendientes
        # drop_pending_updates=True evita que el bot procese mensajes viejos al arrancar
        await bot.delete_webhook(drop_pending_updates=True)
        
        print(f"🚀 BOT INICIADO - ESCUCHA TOTAL ACTIVADA")
        print(f"Filtrando (opcionalmente) por CHAT_ID: {CHAT_ID}")

        # 2. Inicio de Polling
        # Agregamos 'allowed_updates' completo para asegurar que Telegram envíe eventos de grupo
        await dp.start_polling(
            bot,
            allowed_updates=["message", "edited_message", "callback_query", "my_chat_member"],
            polling_timeout=5
        )
    except asyncio.CancelledError:
        print("🛑 Tarea de Telegram cancelada")
    except Exception as e:
        print(f"❌ Error crítico en el Bot: {e}")
    finally:
        # Cierre limpio de la sesión de red
        await bot.session.close()
        print("✅ Conexión con Telegram cerrada.")