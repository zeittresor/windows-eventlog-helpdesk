from __future__ import annotations

import os
import time
from threading import Event
from typing import Callable


def list_voices() -> list[tuple[str, str]]:
    if os.name != "nt":
        return []
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return []
    pythoncom.CoInitialize()
    try:
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        result: list[tuple[str, str]] = []
        for token in voice.GetVoices():
            result.append((str(token.Id), str(token.GetDescription())))
        return result
    finally:
        pythoncom.CoUninitialize()


def speak_text(
    text: str,
    *,
    voice_id: str = "",
    rate: int = 0,
    volume: int = 100,
    cancel_event: Event | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    chunk_callback=None,
) -> bool:
    del chunk_callback
    if os.name != "nt":
        raise RuntimeError("Windows SAPI text-to-speech is only available on Windows.")
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("pywin32 is required for Windows text-to-speech.") from exc
    cancel_event = cancel_event or Event()
    progress_callback = progress_callback or (lambda _v, _t: None)
    status_callback = status_callback or (lambda _s: None)
    pythoncom.CoInitialize()
    try:
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voice.Rate = max(-10, min(10, int(rate)))
        voice.Volume = max(0, min(100, int(volume)))
        if voice_id:
            for token in voice.GetVoices():
                if str(token.Id) == voice_id:
                    voice.Voice = token
                    break
        status_callback("Speaking with Windows SAPI…")
        progress_callback(0, 0)
        voice.Speak(text, 1)  # SVSFlagsAsync
        while not voice.WaitUntilDone(100):
            if cancel_event.is_set():
                voice.Speak("", 3)  # purge + async
                return False
            time.sleep(0.02)
        return True
    finally:
        pythoncom.CoUninitialize()
