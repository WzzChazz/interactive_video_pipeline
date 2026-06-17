import os
import time
import jwt
from dotenv import load_dotenv

load_dotenv(override=True)

KLING_AK = os.getenv("KLING_AK")
KLING_SK = os.getenv("KLING_SK")

def encode_jwt_token(ak, sk):
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": ak,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    # Pass algorithm explicitly
    return jwt.encode(payload, sk, algorithm="HS256", headers=headers)

token = encode_jwt_token(KLING_AK, KLING_SK)
print("Token:", token)
