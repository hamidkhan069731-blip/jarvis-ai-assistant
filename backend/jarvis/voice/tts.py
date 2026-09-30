"""Text-to-speech via pyttsx3 (SAPI5 on Windows).

pyttsx3's run loop is not re-entrant, so we serialise all speech through a lock
and build a fresh engine per utterance in a worker thread — the most robust
pattern for repeated calls.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

from jarvis.config import settings
from jarvis.security.audit import get_logger

log = get_logger("voice.tts")

_lock = threading.Lock()


class TTSEngine:
    def available(self) -> bool:
        try:
            import pyttsx3  # noqa: F401
            return True
        except Exception:
            return False

    def voices(self) -> list[dict[str, Any]]:
        try:
            import pyttsx3
            engine = pyttsx3.init()
            out = [{"id": v.id, "name": getattr(v, "name", v.id)}
                   for v in engine.getProperty("voices")]
            engine.stop()
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("could not list voices: %s", exc)
            return []

    def speak(self, text: str) -> bool:
        """Blocking speak on the local speakers. Returns success."""
        if not text:
            return False
        if not settings.get("tts_enabled", True):
            return False
        with _lock:
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", int(settings.get("tts_rate", 175)))
                engine.setProperty("volume", float(settings.get("tts_volume", 1.0)))
                voice_id = settings.get("tts_voice", "")
                if voice_id:
                    engine.setProperty("voice", voice_id)
                engine.say(text)
                engine.runAndWait()
                engine.stop()
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("TTS failed: %s", exc)
                return False

    def save_to_file(self, text: str, path: str) -> bool:
        with _lock:
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", int(settings.get("tts_rate", 175)))
                engine.save_to_file(text, path)
                engine.runAndWait()
                engine.stop()
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("TTS save failed: %s", exc)
                return False


_tts: Optional[TTSEngine] = None


def get_tts() -> TTSEngine:
    global _tts
    if _tts is None:
        _tts = TTSEngine()
    return _tts
