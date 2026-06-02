import dashscope
from dashscope.audio.tts import SpeechSynthesizer
from config.settings import DASHSCOPE_API_KEY
import os

dashscope.api_key = DASHSCOPE_API_KEY
vid = os.getenv("DASHSCOPE_VOICE_TERRIFIED", "cosyvoice-v3.5-plus-bailian-13d24217b6514e42a85c8ad031c97be5")

print("Testing REST API TTS...")
result = SpeechSynthesizer.call(
    model='cosyvoice-v1',
    voice=vid,
    text='这是一次测试。',
    sample_rate=16000,
    format='mp3'
)

if result.get_audio_data() is not None:
    with open('/Users/mac/Desktop/test_rest.mp3', 'wb') as f:
        f.write(result.get_audio_data())
    print("SUCCESS")
else:
    print(f"FAILED: {result.message}")
