import logging

# Logger global reutilizable
logger = logging.getLogger("binance_ws")
logger.setLevel(logging.INFO)

# Formato opcional si deseas escribir a archivo o consola
# Puedes activarlo si lo necesitas luego
# handler = logging.StreamHandler()
# formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
# handler.setFormatter(formatter)
# logger.addHandler(handler)
