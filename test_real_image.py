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
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

url = "https://api-beijing.klingai.com/v1/videos/image2video"

# Valid 1x1 transparent PNG base64
valid_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

try:
    r = requests.post(url, headers=headers, json={"model_name": "kling-v1", "image": valid_b64, "prompt": "test prompt"}, timeout=5)
    print("Result:", r.status_code, r.text)
except Exception as e:
    print("Error:", e)
