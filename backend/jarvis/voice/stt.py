"""Speech-to-text (server side).

Uses SpeechRecognition with a microphone backend when one is present. A mic
backend (PyAudio) is an optional native dependency; if it is missing we report
capability honestly. The shipped UI performs STT in the browser (Web Speech
API), which is why the server path is optional.
"""
from __future__ import annotations

from typing import Optional

from jarvis.security.audit import get_logger

log = get_logger("voice.stt")


class STTEngine:
    def available(self) -> bool:
        try:
            import speech_recognition  # noqa: F401
        except Exception:
            return False
        return self._has_microphone()

    def _has_microphone(self) -> bool:
        try:
            import speech_recognition as sr
            # Microphone requires PyAudio; importing the class isn't enough.
            names = sr.Microphone.list_microphone_names()
            return bool(names)
        except Exception:
            return False

    def status(self) -> dict:
        try:
            import speech_recognition  # noqa: F401
            have_sr = True
        except Exception:
            have_sr = False
        return {
            "speech_recognition_installed": have_sr,
            "microphone_available": self._has_microphone(),
            "note": ("Server STT ready." if self.available()
                     else "Server STT needs PyAudio + a microphone. "
                          "The UI uses the browser's speech recognition instead."),
        }

    def listen_once(self, timeout: float = 6.0, phrase_limit: float = 10.0) -> Optional[str]:
        """Capture one utterance from the default mic and transcribe it."""
        if not self.available():
            return None
        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.4)
                audio = recognizer.listen(source, timeout=timeout,
                                          phrase_time_limit=phrase_limit)
            return recognizer.recognize_google(audio)  # online; free tier
        except Exception as exc:  # noqa: BLE001
            log.info("STT listen failed: %s", exc)
            return None


_stt: Optional[STTEngine] = None


def get_stt() -> STTEngine:
    global _stt
    if _stt is None:
        _stt = STTEngine()
    return _stt
