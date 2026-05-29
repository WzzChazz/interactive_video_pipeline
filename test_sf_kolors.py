import requests

key = "sk-smtygekjoaioknbhvlvzemhjrbikjtedgddluygfglyrqrft"
headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

payload = {
    "model": "Kwai-Kolors/Kolors",
    "prompt": "a beautiful cyberpunk city",
    "image_size": "576x1024",
    "batch_size": 1,
}
print("Testing Kolors...")
resp = requests.post("https://api.siliconflow.cn/v1/images/generations", json=payload, headers=headers)
print("Status:", resp.status_code)
print("Response:", resp.text)
