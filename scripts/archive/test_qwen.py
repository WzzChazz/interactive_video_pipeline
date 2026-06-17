import os
import dashscope
from dashscope.audio.tts_v2 import ResultCallback
from dashscope.audio.qwen_tts_realtime import QwenTtsRealtime
from dotenv import load_dotenv

load_dotenv(override=True)
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

class FileCallback(ResultCallback):
    def __init__(self, file_path):
        self.file_path = file_path
        self.file = None

    def on_open(self):
        self.file = open(self.file_path, "wb")
        print("Opened")

    def on_data(self, data: bytes):
        if self.file:
            self.file.write(data)

    def on_complete(self):
        print("Complete")
        if self.file:
            self.file.close()

    def on_error(self, message: str):
        print(f"Error: {message}")
        if self.file:
            self.file.close()

cb = FileCallback("test_qwen.mp3")
print("Testing QwenTtsRealtime...")
synthesizer = QwenTtsRealtime(
    model="qwen3-tts-vd-2026-01-26", 
    voice="qwen-tts-vd-bailian-voice-20260522170128737-9234", 
    callback=cb
)
synthesizer.call("测试一下")
print("Size:", os.path.getsize("test_qwen.mp3"))

