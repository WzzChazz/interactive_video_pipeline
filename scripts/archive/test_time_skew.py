import os
import time
import jwt
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
        "exp": 2000000000,  # Year 2033
        "nbf": 1600000000   # Year 2020
    }
    return jwt.encode(payload, sk, algorithm="HS256", headers=headers)

token = generate_jwt(AK, SK)

url = "https://api.klingai.com/v1/videos/image2video"
r = requests.get(url, headers={"Authorization": f"Bearer {token}"})
print(r.status_code, r.text)
