import requests

key = "sk-smtygekjoaioknbhvlvzemhjrbikjtedgddluygfglyrqrft"
headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

# Test FLUX.1-dev
payload_dev = {
    "model": "black-forest-labs/FLUX.1-dev",
    "prompt": "a beautiful cyberpunk city",
    "image_size": "576x1024",
    "batch_size": 1,
    "num_inference_steps": 28,
}

print("Testing FLUX.1-dev...")
resp_dev = requests.post("https://api.siliconflow.cn/v1/images/generations", json=payload_dev, headers=headers)
print("Dev Status:", resp_dev.status_code)
print("Dev Response:", resp_dev.text)

# Test FLUX.1-schnell
payload_schnell = {
    "model": "black-forest-labs/FLUX.1-schnell",
    "prompt": "a beautiful cyberpunk city",
    "image_size": "576x1024",
    "batch_size": 1,
    "num_inference_steps": 4,
}
print("\nTesting FLUX.1-schnell...")
resp_schnell = requests.post("https://api.siliconflow.cn/v1/images/generations", json=payload_schnell, headers=headers)
print("Schnell Status:", resp_schnell.status_code)
print("Schnell Response:", resp_schnell.text)
