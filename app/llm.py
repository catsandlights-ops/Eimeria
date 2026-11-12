"""Client for a local AI model running in Ollama (https://ollama.com). No API costs.

Reliability features:
- Structured answers: each request sends a JSON schema (Ollama 0.5+), so the model must answer in
  exactly the expected shape. Older Ollama versions fall back to plain JSON mode automatically.
- One retry when the reply isn't valid JSON or is missing required fields.
- Statistics (calls, failures, average seconds per answer) for the AI tab.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

import requests

log = logging.getLogger(__name__)


class LocalLLM:
    def __init__(self, url: str, model: str, temperature: float = 0.2, timeout: int = 120):
        self.url = url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self._schema_ok = True              # set False if this Ollama is too old for schemas
        self._lock = threading.Lock()
        self.stats = {"calls": 0, "failures": 0, "retries": 0, "seconds": 0.0}

    def status(self) -> tuple[bool, str]:
        """Is Ollama running and is the chosen model downloaded?"""
        try:
            names = self.installed_models()
            if not any(n == self.model or n.split(":")[0] == self.model for n in names):
                return False, f"Ollama is running but model '{self.model}' is not downloaded. Run: ollama pull {self.model}"
            return True, f"AI ready ({self.model})"
        except Exception:
            return False, "Ollama is not running. Start the Ollama app."

    def installed_models(self) -> list[str]:
        r = requests.get(f"{self.url}/api/tags", timeout=5)
        r.raise_for_status()
        return sorted(m.get("name", "") for m in r.json().get("models", []))

    def with_model(self, model: str) -> "LocalLLM":
        """A copy that uses another model (for comparing models)."""
        return LocalLLM(self.url, model, self.temperature, self.timeout)

    def chat(self, system: str, user: str, fmt=None) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if fmt is not None:
            payload["format"] = fmt
        r = requests.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json()["message"]["content"]

    def chat_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        """Ask for a JSON answer. With `schema`, the reply must match it (required fields checked)."""
        t0 = time.time()
        required = (schema or {}).get("required", [])
        last_err = None
        for attempt in range(2):
            fmt = schema if (schema and self._schema_ok) else "json"
            try:
                text = self.chat(system, user, fmt)
            except requests.HTTPError as e:
                if fmt != "json" and e.response is not None and e.response.status_code in (400, 500):
                    self._schema_ok = False          # old Ollama: no schema support, use JSON mode
                    continue
                self._record(t0, failed=True)
                raise
            except Exception:
                self._record(t0, failed=True)
                raise
            res = parse_json(text)
            if res and all(k in res for k in required):
                self._record(t0, retried=attempt > 0)
                return res
            last_err = text[:200]
            user = user + "\n\nYour previous answer was not valid. Reply with ONLY the JSON object."
        self._record(t0, failed=True, retried=True)
        log.warning("AI gave no valid answer after a retry: %s", last_err)
        return {}

    def _record(self, t0: float, failed: bool = False, retried: bool = False):
        with self._lock:
            self.stats["calls"] += 1
            self.stats["failures"] += int(failed)
            self.stats["retries"] += int(retried)
            self.stats["seconds"] += time.time() - t0

    def summary(self) -> str:
        s = self.stats
        if not s["calls"]:
            return "No AI requests yet."
        return (f"{s['calls']} requests, {s['seconds'] / s['calls']:.1f} s average, "
                f"{s['failures']} failed ({s['failures'] / s['calls']:.0%}), {s['retries']} needed a retry")


def parse_json(text: str) -> dict:
    """Parse JSON from a model reply, tolerating extra text around it (e.g. <think> blocks)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                out = json.loads(m.group(0))
                return out if isinstance(out, dict) else {}
            except json.JSONDecodeError:
                pass
    log.warning("AI returned invalid JSON: %s", (text or "")[:200])
    return {}
