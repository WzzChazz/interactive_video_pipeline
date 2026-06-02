import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback
from config.settings import DASHSCOPE_API_KEY
import os
import threading

dashscope.api_key = DASHSCOPE_API_KEY
vid = os.getenv("DASHSCOPE_VOICE_TERRIFIED", "cosyvoice-v3.5-plus-bailian-13d24217b6514e42a85c8ad031c97be5")

class TestCallback(ResultCallback):
    def __init__(self):
        self._event = threading.Event()
        self.error = None
    def on_error(self, message):
        self.error = message
        self._event.set()
    def on_complete(self):
        self._event.set()

cb = TestCallback()
synthesizer = SpeechSynthesizer(
    model='cosyvoice-v3.5-plus',
    voice=vid,
    speech_rate=1.5  # Test if speech_rate triggers 418
)
synthesizer.call('测试语速功能是否被引擎支持')
cb._event.wait(10)
print(f"Error with speech_rate: {cb.error}")
