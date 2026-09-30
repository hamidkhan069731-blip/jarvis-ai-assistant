"""Voice engine (server side).

TTS uses pyttsx3 (Windows SAPI5) and actually speaks on the local machine's
speakers — appropriate for a desktop assistant. STT via SpeechRecognition needs
a microphone backend (PyAudio); when unavailable the module reports that clearly
instead of pretending. The primary voice path in the shipped UI is the browser
Web Speech API (see frontend), which needs no extra native dependencies.
"""
from jarvis.voice.tts import TTSEngine, get_tts
from jarvis.voice.stt import STTEngine, get_stt

__all__ = ["TTSEngine", "get_tts", "STTEngine", "get_stt"]
