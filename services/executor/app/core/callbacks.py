"""Java 治理层回调：解析进度 / 完成 / 失败。"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CallbackClient:
    def __init__(self, base_url: str, secret: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.client = httpx.AsyncClient(timeout=timeout)

    async def send(self, task_id: str, event: str, seq: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            {"event": event, "seq": seq, "payload": payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.secret.encode("utf-8"),
            f"{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        try:
            response = await self.client.post(
                f"{self.base_url}/api/internal/callbacks/{task_id}",
                content=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Timestamp": timestamp,
                    "X-Signature": signature,
                },
            )
            if response.status_code >= 400:
                logger.warning(
                    "governor_callback_failed task=%s event=%s status=%s",
                    task_id,
                    event,
                    response.status_code,
                )
        except Exception as exc:
            logger.warning("governor_callback_unavailable task=%s event=%s: %s", task_id, event, exc)

    async def aclose(self) -> None:
        await self.client.aclose()
