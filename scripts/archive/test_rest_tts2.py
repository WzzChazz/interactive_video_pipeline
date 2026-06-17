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
        self.success = False
        self.error = None
    def on_complete(self):
        self.success = True
        self._event.set()
    def on_error(self, message):
        self.error = message
        self._event.set()

cb = TestCallback()
synthesizer = SpeechSynthesizer(
    model='cosyvoice-v1',
    voice=vid,
    speech_rate=1.15
    # NOTICE: NO instruction parameter!!
)
synthesizer.call('这是移除 instruction 之后的测试！')
cb._event.wait(10)
print(f"Success: {cb.success}, Error: {cb.error}")
