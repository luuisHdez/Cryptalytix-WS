import boto3
import json
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


async def trigger_apigateway_async(config, operation):
    print(f"🟢 Ejecutando trigger_apigateway_async con config y operación: {operation}, {config}")
    
    session = boto3.Session(profile_name='admin-user')
    url = "https://f9gbw5irel.execute-api.us-east-1.amazonaws.com/default/twilio-lambda"
    credentials = session.get_credentials()
    region = session.region_name or "us-east-1"

    request = AWSRequest(
        method="POST",
        url=url,
        data=json.dumps({**config, "operation": operation}),  # Combina config y operación
        headers={"Content-Type": "application/json"}
    )
    SigV4Auth(credentials, "execute-api", region).add_auth(request)

    headers = dict(request.headers)
    body = request.body

    async with httpx.AsyncClient() as client:
        try:
            print(f"📤 Enviando POST firmado a {url}")
            response = await client.post(url, data=body, headers=headers, timeout=10.0)
            print(f"✅ STATUS: {response.status_code}, RESPUESTA: {response.text}")
        except Exception as e:
            print(f"❌ Error al llamar a Lambda: {e}")
