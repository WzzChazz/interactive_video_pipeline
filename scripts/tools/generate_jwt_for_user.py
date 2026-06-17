import time
import jwt
import json

AK = "AFfLgbtL4rPJfBNpJE9RMg4HaQpQCFYT"
SK = "PtPKPLpLL4nPTLaAfaGHEgdAgrALMThA"

now = int(time.time())
exp = now + 1800
nbf = now - 5

headers = {"alg": "HS256", "typ": "JWT"}
payload = {
    "iss": AK,
    "exp": exp,
    "nbf": nbf
}

token = jwt.encode(payload, SK, algorithm="HS256", headers=headers)

print("=== JWT GENERATION DETAILS ===")
print("AK (iss):", AK)
print("SK (Secret):", SK)
print("nbf (Not Before):", nbf)
print("exp (Expires):", exp)
print("\n=== PAYLOAD (JSON) ===")
print(json.dumps(payload, indent=2))
print("\n=== JWT TOKEN ===")
print(token)
