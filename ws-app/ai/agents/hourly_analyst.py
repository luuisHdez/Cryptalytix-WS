import asyncio, os
from datetime import datetime, timezone
from groq import Groq
from utils.redis_utils import redis_client
from ai.mcp.tools import get_market_technical_context
from services.telegram_bot import bot

client = Groq(api_key=os.getenv('GROQ_API_KEY'))
GROQ_MODEL = "qwen/qwen3-32b"
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def agent_analysis():
    """
    Analiza símbolos detectados en Redis usando datos multi-temporales de Binance.
    """
    while True:
        try:
            print("\n [AGENTE IA] Iniciando escaneo de mercado...")
            
            all_keys = redis_client.keys("*")
            market_symbols = []
            
            for key in all_keys:
                k_str = key.decode() if isinstance(key, bytes) else key
                if k_str.isupper() and "_" not in k_str:
                    market_symbols.append(k_str)

            print(f" Símbolos detectados en Redis: {market_symbols}")

            if not market_symbols:
                print("💤 No se encontraron símbolos en Redis.")
            else:
                macro_context = await fetch_macro_news()
                
                for symbol in market_symbols:
                    # 3. devuelve 10d, 20h y 20 de 15m
                    tech_context = get_market_technical_context(symbol)
                    print(tech_context)
                    if isinstance(tech_context, dict) and "error" in tech_context:
                        print(f" Error en {symbol}: {tech_context['error']}")
                        continue
                    
                    price = tech_context.get('current_price', 'N/A')
                    rvol = tech_context.get('rvol', 'N/A')
                    # Extraemos la data extendida para el prompt
                    data_ext = tech_context.get('data_macro', {})

                    d10_clean = ", ".join(data_ext.get('d10', []))
                    h20_clean = ", ".join(data_ext.get('h20', []))
                    m15_clean = ", ".join(data_ext.get('m20', []))
                    try:
                        prompt = f"""
[DATASET TÉCNICO: {symbol}]
- Info Externa: {macro_context}
- Serie 1D: {d10_clean}
- Serie 1H: {h20_clean}
- Serie 15M: {m15_clean}
- Snapshot: Precio {price} | RVOL {rvol}

[INSTRUCCIONES DE EJECUCIÓN]
Eres un motor de análisis algorítmico. Prohibido mencionar Bitcoin, ETFs o sucesos externos a menos que aparezcan explícitamente en "Info Externa".

1. ANÁLISIS MTF: ¿El precio de {symbol} en 15M es consistente con la serie de 1D? Define si hay alineación o divergencia.
2. VOLUMEN CRÍTICO: Interpreta el RVOL de {rvol}. Si es < 1.0, califícalo como falta de interés. Si es > 1.5, califícalo como participación activa.
3. SETUP OPERATIVO: Determina si hay claridad técnica. Si los datos son erráticos, recomienda "No Operar".
4. DIRECCIÓN: Define el sesgo (Bullish/Bearish/Neutral) basándote SOLO en los números de las series temporales.

[RESTRICCIONES]
- NO repitas los datos de entrada.
- NO menciones otros activos que no sean {symbol}.
- NO uses etiquetas <think>.
- Máximo 1200 caracteres.
"""
                        
                        response = client.chat.completions.create(
                            model=GROQ_MODEL,
                            messages=[{"role": "user", "content": prompt}]
                        )

                        raw_content = response.choices[0].message.content
                        #print(raw_content)
                        # 1. FILTRADO RADICAL: Eliminar todo lo que esté dentro de <think>
                        if "</think>" in raw_content:
                            # Nos quedamos solo con lo que está después de cerrar el pensamiento
                            report_text = raw_content.split("</think>")[-1].strip()
                        else:
                            report_text = raw_content

                        # 2. SEGUNDA SEGURIDAD: Si por alguna razón el texto sigue siendo gigante
                        if len(report_text) > 3800:
                            report_text = report_text[:3800] + "\n\n(Análisis truncado por longitud)"

                        # 3. ENVÍO LIMPIO
                        await bot.send_message(
                            chat_id=CHAT_ID, 
                            text=f" ANÁLISIS TÉCNICO: {symbol}*\n\n{report_text}", 
                            parse_mode="Markdown"
)
                        print(f" Reporte Multi-Timeframe enviado para {symbol}")

                    except Exception as ai_err:
                        print(f" Error en Groq/Telegram: {ai_err}")

            print("✅ Escaneo completado. Próximo ciclo en 1 hora.\n")
            await asyncio.sleep(3600)

        except Exception as e:
            print(f" Error crítico: {e}")
            await asyncio.sleep(60)

async def fetch_macro_news():
    """
    Investiga eventos macro de la semana y analiza la volatilidad inmediata
    según la apertura/cierre de mercados globales.
    """
    now = datetime.now()
    fecha_actual = now.strftime('%A, %d de %B de %Y')
    hora_actual = now.strftime('%H:%M UTC') # Es importante especificar UTC
    
    prompt = f"""
    Hoy es {fecha_actual} y son las {hora_actual}.
    
    [RETROSPECTIVA SEMANAL - De dónde venimos]
    1. HITOS DE LA SEMANA: Resume qué eventos o datos ya ocurrieron desde el lunes que marcaron el sesgo actual (ej: subastas del Tesoro, datos de empleo publicados o cierres de velas previos). ¿El precio está reaccionando a un evento pasado o anticipando uno futuro?
    2. ACCIÓN DEL PRECIO PREVIA: ¿Cómo abrimos la semana? Identifica si estamos cotizando por encima o por debajo del precio de apertura semanal y qué niveles de liquidez ya han sido "barridos".

    [TAREA DE CONTEXTO GLOBAL - Dónde estamos y hacia dónde vamos]
    1. PANORAMA ACTUAL: Identifica los 3 factores que mueven el sentimiento ahora. Explica la expectativa de lo que falta por ocurrir (CPI, flujos ETF, geopolítica) sin que eclipse el análisis técnico.
    2. MAPA DE LIQUIDEZ Y SESIÓN: Según la hora ({hora_actual}), define la sesión dominante (Asia/London/NY) y el estado del 'Order Flow'. ¿Hay convicción institucional o participación minoritaria?
    3. RÉGIMEN DE VOLATILIDAD: Define si el régimen es 'Risk-On' o 'Risk-Off'. ¿La apertura/cierre de las bolsas próximas sugiere un cambio de ritmo inminente?

    RESTRICCIONES:
    - Visión holística: Conecta lo que ya pasó con lo que está pasando.
    - No repitas listas de precios crudos.
    - Párrafos cortos y analíticos (mesa de trading).
    - Prohibido usar <think>.
"""
    
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system", 
                    "content": "Eres un estratega jefe de un fondo de cobertura cripto. Tu análisis debe conectar la política monetaria con la liquidez inmediata del mercado."
                },
                {"role": "user", "content": prompt}
            ]
        )
        
        content = response.choices[0].message.content
        # Limpieza de seguridad para modelos de razonamiento
        if "</think>" in content:
            content = content.split("</think>")[-1].strip()
            print("respuesta de consuta a macro",content)
        return content
    except Exception as e:
        print(f" Error obteniendo macro/sesión: {e}")
        return "Sentimiento neutral. Mercados operando con volumen estándar."