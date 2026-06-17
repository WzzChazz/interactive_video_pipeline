import os
import requests
from config.settings import DASHSCOPE_API_KEY

vid = os.getenv("DASHSCOPE_VOICE_TERRIFIED", "cosyvoice-v3.5-plus-bailian-13d24217b6514e42a85c8ad031c97be5")

url = 'https://dashscope.aliyuncs.com/api/v1/services/audio/text-to-speech/text-to-speech'
headers = {
    'Authorization': f'Bearer {DASHSCOPE_API_KEY}',
    'Content-Type': 'application/json'
}
data = {
    "model": "cosyvoice-v1",
    "input": {
        "text": "这是一次纯HTTP绕过测试。"
    },
    "parameters": {
        "voice": vid
    }
}

try:
    response = requests.post(url, headers=headers, json=data, timeout=30)
    if response.status_code == 200 and response.headers.get('Content-Type') in ['audio/mpeg', 'audio/mp3']:
        with open('/Users/mac/Desktop/test_pure_http.mp3', 'wb') as f:
            f.write(response.content)
        print("SUCCESS! File saved to Desktop.")
    else:
        print(f"FAILED: HTTP {response.status_code} - {response.text}")
except Exception as e:
    print(f"ERROR: {e}")
