import os
from dotenv import load_dotenv
import requests

load_dotenv(".env")
key = os.getenv("FLUX_API_KEY")

payload = {
    "model": "stabilityai/stable-diffusion-3-5-large",
    "prompt": "a beautiful cyberpunk city",
    "image_size": "576x1024",
    "batch_size": 1,
    "num_inference_steps": 28,
}
headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
resp = requests.post("https://api.siliconflow.cn/v1/images/generations", json=payload, headers=headers)
print("Status Code:", resp.status_code)
print("Response:", resp.text)
