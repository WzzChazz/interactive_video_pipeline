import jwt
import requests
import time

AK = "AFfLgbtL4rPJfBNpJE9RMg4HaQpQCFYT"
SK = "PtPKPLpLL4nPTLaAfaGHEgdAgrALMThA"

now = int(time.time())
payload = {
    "iss": AK,
    "exp": now + 1800,
    "nbf": now - 5
}
# By default, PyJWT adds typ="JWT". We can't easily remove it via public API unless we craft it manually or PyJWT allows it.
token = jwt.encode(payload, SK, algorithm="HS256") # Default header

url = "https://api.klingai.com/v1/videos/image2video"
r = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"model_name": "kling-v1", "image": "test", "prompt": "test"})
print(r.status_code, r.text)
