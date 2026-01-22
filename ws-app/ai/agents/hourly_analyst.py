import asyncio
import os
from datetime import datetime, timezone
from groq import Groq

from utils.redis_utils import redis_client
from ai.mcp.tools import get_market_technical_context
from services.telegram_bot import bot

# ======================================================
# CONFIG
# ======================================================
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
GROQ_MODEL = "qwen/qwen3-32b"
CHAT_ID = os.getenv("TELEGRAM_ANALYSIS")

# ======================================================
# AGENTE MACRO — CONTEXTO PURO
# ======================================================
async def fetch_macro_news():
    now = datetime.now(timezone.utc)
    hora_actual = now.strftime("%H:%M UTC")

    prompt = f"""
Hora actual: {hora_actual}

ROL:
Eres un analista macro institucional.

CLASIFICA UNICAMENTE:

SESION:
ASIA / LONDON / NY

REGIMEN:
RISK_ON / RISK_OFF / NEUTRAL

LIQUIDEZ:
ALTA / MEDIA / BAJA

REGLAS:
No narrativa
No precios
No sesgo operativo
No think
Max 5 lineas
"""

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.split("</think>")[-1].strip()
    except Exception:
        return "SESION UNKNOWN\nREGIMEN NEUTRAL\nLIQUIDEZ MEDIA"

# ======================================================
# AGENTE TECNICO — CLASIFICADOR
# ======================================================
def evaluate_technical(symbol: str, tech: dict) -> str:
    of = tech["order_flow_15m"]

    prompt = f"""
ACTIVO {symbol}
DATOS 15M: PrecioDelta {of['price_delta']}, CvdDelta {of['cvd_delta']}, Rvol {tech['rvol_15m']}

TAREA: 
1. Compara PrecioDelta vs CvdDelta. Si tienen signos opuestos, es ABSORCIÓN/DIVERGENCIA.
2. Si Rvol < 0.8, el INTERES es BAJO (independientemente del movimiento).

CLASIFICA:
FLUJO= [CONFIRMACION/ABSORCION/DISTRIBUCION/RANGO]
INTERES= [BAJO/NORMAL/ALTO]
CLARIDAD= [CLARA/SUCIA] (Sucia si Rvol es bajo o el delta es casi cero)

Sigue estrictamente: FORMATO: FLUJO= INTERES= CLARIDAD=
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.split("</think>")[-1].strip()

# ======================================================
# AGENTE DECISION — UNICO QUE DECIDE
# ======================================================
def final_decision(symbol: str, macro: str, technical: str) -> str:
    prompt = f"""
ACTIVO {symbol}
CONTEXTO: Macro {macro} | Técnico {technical}

REGLAS CRÍTICAS DE OPERACIÓN:
- Si INTERES=BAJO, la ACCION siempre es NO_OPERAR (falta de liquidez/participación).
- Si CLARIDAD=SUCIA, la ACCION siempre es NO_OPERAR.
- Si REGIMEN=RISK_OFF y SESGO=BULLISH, ser extremadamente exigente con la CLARIDAD.
- Para OPERAR, el FLUJO debe estar alineado con el SESGO (ej: Distribución = Bearish).

DECIDE:
SESGO= [BULLISH/BEARISH/NEUTRAL]
ACCION= [OPERAR/NO_OPERAR]
MOTIVO= (Explica en 1 frase corta por qué se descarta o se acepta)
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.split("</think>")[-1].strip()

# ======================================================
# AGENTE CONTEXTO — EXPLICACION HUMANA
# ======================================================
def explain_context(symbol: str, macro: str, tech: dict, tech_eval: str, decision: str) -> str:
    of = tech["order_flow_15m"]

    prompt = f"""
...
EXPLICA:
1. ¿Qué indica la relación entre el movimiento del precio ({of['price_delta']}) y el esfuerzo del volumen ({of['cvd_delta']})? (Ej: ¿Esfuerzo sin resultado?)
2. Justifica la decisión basada en el Rvol ({tech['rvol_15m']}). Si es bajo, advierte sobre la trampa de liquidez.
3. Conecta el régimen macro con el comportamiento actual del activo.

ESTILO: Profesional, directo, tipo terminal Bloomberg.
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.split("</think>")[-1].strip()

# ======================================================
# LOOP PRINCIPAL
# ======================================================
async def agent_analysis():
    while True:
        try:
            keys = redis_client.keys("*")
            symbols = [
                k.decode() if isinstance(k, bytes) else k
                for k in keys
                if k.isupper() and "_" not in k
            ]

            if not symbols:
                await asyncio.sleep(3600)
                continue

            macro = await fetch_macro_news()

            for symbol in symbols:
                tech = get_market_technical_context(symbol)
                print(tech)
                if "error" in tech:
                    continue

                tech_eval = evaluate_technical(symbol, tech)
                decision = final_decision(symbol, macro, tech_eval)
                context = explain_context(symbol, macro, tech, tech_eval, decision)

                message = (
                    "ANALISIS " + symbol + "\n\n"
                    + context + "\n\n"
                    + "DECISION\n"
                    + decision
                )

                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=message
                )

            await asyncio.sleep(3600)

        except Exception as e:
            print("[AGENT ERROR]", e)
            await asyncio.sleep(60)
