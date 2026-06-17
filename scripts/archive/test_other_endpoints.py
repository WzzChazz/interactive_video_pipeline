import os
import time
import jwt
import requests
from dotenv import load_dotenv

load_dotenv(override=True)
AK = os.getenv("KLING_AK").strip()
SK = os.getenv("KLING_SK").strip()

def get_token(ak, sk):
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": ak,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    return jwt.encode(payload, sk, algorithm="HS256", headers=headers)

token = get_token(AK, SK)
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

print(requests.get("https://api.klingai.com/v1/models", headers=headers).text)
