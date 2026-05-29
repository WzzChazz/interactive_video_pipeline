import os
import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback
from dotenv import load_dotenv

load_dotenv(override=True)
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

class FileCallback(ResultCallback):
    def __init__(self, file_path):
        self.file_path = file_path
        self.file = None

    def on_open(self):
        self.file = open(self.file_path, "wb")

    def on_data(self, data: bytes):
        if self.file:
            self.file.write(data)

    def on_complete(self):
        if self.file:
            self.file.close()

    def on_error(self, message: str):
        print(f"Callback Error: {message}")
        if self.file:
            self.file.close()

cb = FileCallback("test_cosy_v35.mp3")

print("Testing cosyvoice-v3.5-plus...")
synthesizer = SpeechSynthesizer(
    model="cosyvoice-v3.5-plus", 
    voice="cosyvoice-v3.5-plus-vd-bailian-f0f1b1bb3679400486ad031fc8bd2bed", 
    instruction="你说话的情感是fearful。",
    callback=cb
)
synthesizer.call("终于修好了，希望能出声音。")
print("File size:", os.path.getsize("test_cosy_v35.mp3"))

