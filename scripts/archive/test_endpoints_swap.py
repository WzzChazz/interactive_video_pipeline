import os
import time
import jwt
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

SK = os.getenv("KLING_AK").strip()  # Swapped
AK = os.getenv("KLING_SK").strip()  # Swapped

def get_token(ak, sk):
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": ak,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    return jwt.encode(payload, sk, algorithm="HS256", headers=headers)

token = get_token(AK, SK)
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

url = "https://api.klingai.com/v1/videos/image2video"

try:
    r = requests.post(url, headers=headers, json={"model_name": "kling-v1", "image": "test", "prompt": "test"}, timeout=5)
    print("Result:", r.status_code, r.text)
except Exception as e:
    print("Error:", e)
