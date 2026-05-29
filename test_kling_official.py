import time
import jwt
import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)
AK = os.getenv("KLING_AK").strip()
SK = os.getenv("KLING_SK").strip()

def generate_jwt(ak, sk):
    headers = {
        "alg": "HS256",
        "typ": "JWT"
    }
    payload = {
        "iss": ak,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    return jwt.encode(payload, sk, algorithm="HS256", headers=headers)

token = generate_jwt(AK, SK)
print("Token:", token)

# Let's try to list models or check balance if Kling has such endpoint
url = "https://api.klingai.com/v1/videos/image2video"
r = requests.get(url, headers={"Authorization": f"Bearer {token}"})
print(r.status_code, r.text)
