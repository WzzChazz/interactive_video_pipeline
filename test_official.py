import time
import jwt

ak = "Ah8JJyGFBr4fbF3aLykNHRnJPLnCKfEE"
sk = "MmYLmMgKMrB8PT4tA3reFFfTMt3gLJa4"

def encode_jwt_token(ak, sk):
    headers = {
        "alg": "HS256",
        "typ": "JWT"
    }
    payload = {
        "iss": ak,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    token = jwt.encode(payload, sk, headers=headers)
    return token

api_token = encode_jwt_token(ak, sk)
print(api_token)
