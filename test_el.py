import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)
API_KEY = os.getenv("ELEVENLABS_API_KEY")

def test_tts(model_id):
    url = "https://api.elevenlabs.io/v1/text-to-speech/IKne3meq5aSn9XLyUdCD"
    headers = {"xi-api-key": API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": "测试一下",
        "model_id": model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.5}
    }
    try:
        r = requests.post(url, json=payload, headers=headers)
        print(f"Model {model_id} -> HTTP {r.status_code}")
        if r.status_code != 200:
            print("Response:", r.text)
    except Exception as e:
        print("Error:", e)

print("Testing eleven_turbo_v2_5...")
test_tts("eleven_turbo_v2_5")

print("Testing eleven_multilingual_v2...")
test_tts("eleven_multilingual_v2")
