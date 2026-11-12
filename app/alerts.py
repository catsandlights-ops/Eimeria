"""Notifications: Windows pop-ups (shown by the window) and optional phone alerts via ntfy.

Phone setup: install the free "ntfy" app (Android/iPhone), tap +, subscribe to a topic
name that nobody could guess (e.g. "eimeria-7f3k9q2x"), and enter the same name in Settings.
Anyone who knows the topic name can read the alerts, so make it long and random.
"""
from __future__ import annotations

import logging
import threading

import requests

log = logging.getLogger(__name__)


class Alerter:
    def __init__(self, cfg: dict, emit=None):
        self.cfg = cfg
        self.emit = emit or (lambda kind, data: None)

    def send(self, title: str, message: str, kind: str = "trade", urgent: bool = False):
        """kind: 'trade', 'problem' or 'report' (each can be switched off in settings)."""
        if not self.cfg.get(f"on_{kind}", True):
            return
        if self.cfg.get("desktop", True):
            self.emit("alert", {"title": title, "message": message, "urgent": urgent})
        topic = (self.cfg.get("ntfy_topic") or "").strip()
        if topic:
            threading.Thread(target=self._ntfy, args=(topic, title, message, urgent), daemon=True).start()

    def _ntfy(self, topic, title, message, urgent):
        try:
            requests.post(f"{self.cfg.get('ntfy_server', 'https://ntfy.sh').rstrip('/')}/{topic}",
                          data=message.encode("utf-8"), timeout=10,
                          headers={"Title": title.encode("ascii", "ignore").decode(),
                                   "Priority": "high" if urgent else "default",
                                   "Tags": "warning" if urgent else "chart_with_upwards_trend"})
        except Exception as e:
            log.warning("Phone alert failed: %s", e)
