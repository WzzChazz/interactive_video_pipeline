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
    voice=vid
)
synthesizer.call('<speak>这<break time="500ms"/>到底是什么东西！</speak>')
cb._event.wait(10)
print(f"Error with SSML: {cb.error}")
