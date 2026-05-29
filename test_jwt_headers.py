import jwt
payload = {"test": "test"}
sk = "secret"
token1 = jwt.encode(payload, sk, algorithm="HS256")
headers = {"alg": "HS256", "typ": "JWT"}
token2 = jwt.encode(payload, sk, algorithm="HS256", headers=headers)
print("Token1:", token1)
print("Token2:", token2)
