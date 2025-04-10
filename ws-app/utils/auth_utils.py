# utils/auth_utils.py

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from fastapi import HTTPException, Request
from starlette.status import HTTP_401_UNAUTHORIZED
import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")  # Debe coincidir con la de Django

def verify_jwt_from_cookie(request: Request):
    token = request.cookies.get("jwt-auth")
    if not token:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Token no encontrado")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except ExpiredSignatureError:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Token expirado")
    except InvalidTokenError:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Token inválido")

