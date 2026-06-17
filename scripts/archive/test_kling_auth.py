import os
import time
import jwt
import requests
from dotenv import load_dotenv

load_dotenv()

KLING_AK = os.getenv("KLING_AK")
KLING_SK = os.getenv("KLING_SK")

def _encode_jwt_token(ak, sk):
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": ak,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    return jwt.encode(payload, sk, headers=headers)

token = _encode_jwt_token(KLING_AK, KLING_SK)
print("Token generated.")

# Try to query a non-existent task just to see auth error vs task not found
url = "https://api.klingai.com/v1/videos/image2video/test_task_id"
headers = {
    "Authorization": f"Bearer {token}"
}
r = requests.get(url, headers=headers)
print("Status:", r.status_code)
print("Response:", r.text)
