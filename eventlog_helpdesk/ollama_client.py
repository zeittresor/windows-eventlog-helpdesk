from __future__ import annotations

import json
from threading import Event
from typing import Callable

import requests


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, endpoint: str, timeout: int = 900) -> None:
        endpoint = endpoint.strip().rstrip("/")
        self.endpoint = endpoint or "http://127.0.0.1:11434"
        self.timeout = max(int(timeout), 30)

    def list_models(self) -> list[str]:
        try:
            response = requests.get(f"{self.endpoint}/api/tags", timeout=(5, 30))
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise OllamaError(f"Could not query Ollama models at {self.endpoint}: {exc}") from exc
        names = [str(item.get("name", "")).strip() for item in payload.get("models", [])]
        return sorted({name for name in names if name}, key=str.casefold)

    def _stream(
        self,
        path: str,
        payload: dict,
        *,
        text_key: str,
        cancel_event: Event | None,
        chunk_callback: Callable[[str], None] | None,
        status_callback: Callable[[str], None] | None,
    ) -> str:
        cancel_event = cancel_event or Event()
        chunk_callback = chunk_callback or (lambda _chunk: None)
        status_callback = status_callback or (lambda _status: None)
        try:
            with requests.post(
                f"{self.endpoint}{path}",
                json=payload,
                stream=True,
                timeout=(10, self.timeout),
            ) as response:
                response.raise_for_status()
                pieces: list[str] = []
                token_chunks = 0
                for raw_line in response.iter_lines(decode_unicode=True):
                    if cancel_event.is_set():
                        response.close()
                        raise OllamaError("Operation cancelled.")
                    if not raw_line:
                        continue
                    try:
                        item = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("error"):
                        raise OllamaError(str(item["error"]))
                    if text_key == "message":
                        text = str((item.get("message") or {}).get("content", ""))
                    else:
                        text = str(item.get(text_key, ""))
                    if text:
                        pieces.append(text)
                        token_chunks += 1
                        chunk_callback(text)
                        if token_chunks % 12 == 0:
                            status_callback(f"Receiving Ollama response… {token_chunks} streamed chunks")
                    if item.get("done"):
                        break
                return "".join(pieces).strip()
        except OllamaError:
            raise
        except requests.RequestException as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        system: str,
        options: dict,
        keep_alive: str,
        cancel_event: Event | None = None,
        chunk_callback: Callable[[str], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> str:
        if not model.strip():
            raise OllamaError("No Ollama model is selected.")
        payload = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "stream": True,
            "options": options,
            "keep_alive": keep_alive,
        }
        return self._stream(
            "/api/generate",
            payload,
            text_key="response",
            cancel_event=cancel_event,
            chunk_callback=chunk_callback,
            status_callback=status_callback,
        )

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict,
        keep_alive: str,
        cancel_event: Event | None = None,
        chunk_callback: Callable[[str], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> str:
        if not model.strip():
            raise OllamaError("No Ollama model is selected.")
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": options,
            "keep_alive": keep_alive,
        }
        return self._stream(
            "/api/chat",
            payload,
            text_key="message",
            cancel_event=cancel_event,
            chunk_callback=chunk_callback,
            status_callback=status_callback,
        )
