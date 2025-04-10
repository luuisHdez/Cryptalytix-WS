import os
import psycopg2
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv(dotenv_path=".env")

# Conexión global a PostgreSQL
def get_db_connection():
    return psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT")
    )
