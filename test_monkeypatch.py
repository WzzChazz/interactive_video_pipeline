from dashscope.audio.tts_v2 import SpeechSynthesizer

def patched_start_stream(self):
    print("MONEKYPATCH ACTIVE: connecting with 30s timeout")
    self._SpeechSynthesizer__connect(30)

SpeechSynthesizer._SpeechSynthesizer__start_stream = patched_start_stream

synthesizer = SpeechSynthesizer(model='cosyvoice-v1', voice='longxiaochun')
try:
    synthesizer.call('测试')
    print("SUCCESS")
except Exception as e:
    print(f"FAILED: {e}")
