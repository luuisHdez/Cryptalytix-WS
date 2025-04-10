import logging
from logging.handlers import RotatingFileHandler

# Configurar el logger global
handler = RotatingFileHandler("server.log", maxBytes=5*1024*1024, backupCount=5)  # 5MB por archivo, 5 backups
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

logger = logging.getLogger("cryptalytix")
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())