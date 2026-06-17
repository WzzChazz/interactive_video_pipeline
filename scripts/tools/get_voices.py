import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)
API_KEY = os.getenv("ELEVENLABS_API_KEY")

headers = {"xi-api-key": API_KEY}
url = "https://api.elevenlabs.io/v1/voices"

try:
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    voices = resp.json().get("voices", [])
    
    print(f"Total voices: {len(voices)}")
    for v in voices[:20]:
        print(f"ID: {v['voice_id']}, Name: {v['name']}, Labels: {v.get('labels', {})}")
except Exception as e:
    print(f"Error: {e}")
