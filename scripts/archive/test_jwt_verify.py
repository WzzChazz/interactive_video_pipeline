import jwt
token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJBRmZMZ2J0TDRyUEpmQk5wSkU5Uk1nNEhhUXBRQ0ZZVCIsImV4cCI6MTc3OTQzMzQ3OSwibmJmIjoxNzc5NDMxNjc0fQ.FGaxMxzHfjiHZZj-pvP2OOC3MUSmFXWzcMKsMBiNmTA"
sk = "PtPKPLpLL4nPTLaAfaGHEgdAgrALMThA"
try:
    # Disable expiration check just to test signature
    decoded = jwt.decode(token, sk, algorithms=["HS256"], options={"verify_exp": False, "verify_nbf": False})
    print("Python Verification SUCCESS! Payload:", decoded)
except Exception as e:
    print("Python Verification FAILED!", e)
