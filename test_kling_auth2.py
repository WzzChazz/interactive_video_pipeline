import os
import time
import jwt
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

KLING_AK = os.getenv("KLING_AK")
KLING_SK = os.getenv("KLING_SK")
print("AK:", KLING_AK)

def encode_jwt_token(ak, sk):
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": ak,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    return jwt.encode(payload, sk, headers=headers)

token = encode_jwt_token(KLING_AK, KLING_SK)
url = "https://api.klingai.com/v1/videos/image2video/test_task_id"
headers = {"Authorization": f"Bearer {token}"}
r = requests.get(url, headers=headers)
print("Status:", r.status_code)
print("Response:", r.text)
