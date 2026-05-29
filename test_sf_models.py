import requests

key = "sk-smtygekjoaioknbhvlvzemhjrbikjtedgddluygfglyrqrft"
headers = {"Authorization": f"Bearer {key}"}

resp = requests.get("https://api.siliconflow.cn/v1/models", headers=headers)
if resp.status_code == 200:
    models = resp.json().get("data", [])
    # Filter image generation models or ones containing 'flux' or 'sd'
    flux_models = [m["id"] for m in models if "flux" in m["id"].lower() or "stable-diffusion" in m["id"].lower() or m.get("type") == "image"]
    print("Available image models:")
    for m in flux_models:
        print(" -", m)
else:
    print("Failed to fetch models:", resp.text)
