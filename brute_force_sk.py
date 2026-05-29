import os
import time
import jwt
import requests
import itertools
from concurrent.futures import ThreadPoolExecutor

AK = "AFfLgbtL4rPJfBNpJE9RMg4HaQpQCFYT"
base_sk = list("PtPKPLpLL4nPTLaAfaGHEgdAgrALMThA")
ambiguous_indices = [5, 7, 8, 13, 27]

combinations = list(itertools.product(['L', 'l', 'I'], repeat=len(ambiguous_indices)))
print(f"Testing {len(combinations)} SK combinations...")

url = "https://api.klingai.com/v1/videos/image2video"

# Fixed payload so we can pre-generate some of the request
def test_sk(comb):
    sk_chars = base_sk.copy()
    for idx, val in zip(ambiguous_indices, comb):
        sk_chars[idx] = val
    sk = "".join(sk_chars)
    
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": AK,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5
    }
    token = jwt.encode(payload, sk, algorithm="HS256", headers=headers)
    
    req_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    # fast timeout
    try:
        r = requests.post(url, headers=req_headers, json={"model_name": "kling-v1", "image": "test", "prompt": "test"}, timeout=2)
        if r.status_code == 400 or (r.status_code == 401 and "Auth failed" not in r.text):
            # If it's 400 Bad Request, auth passed!
            # If it's 401 but not Auth failed (e.g. invalid balance), auth passed!
            return sk, r.status_code, r.text
        if r.status_code == 200:
            return sk, r.status_code, r.text
    except:
        pass
    return None

with ThreadPoolExecutor(max_workers=10) as executor:
    results = executor.map(test_sk, combinations)

for res in results:
    if res:
        print("!!! FOUND MATCH !!!")
        print("Correct SK:", res[0])
        print("Response:", res[1], res[2])
        break
else:
    print("No match found among L/l/I combinations.")
