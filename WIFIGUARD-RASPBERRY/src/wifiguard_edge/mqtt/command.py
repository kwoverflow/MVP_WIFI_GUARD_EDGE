"""하향 명령 처리 (F-P13) — `cmd` 구독 → 실행 → `ack` 발행.

## 명령은 즉시 ack 하고 별도 스레드에서 실행한다

캘리브레이션은 61초짜리다. paho 의 `on_message` 콜백 안에서 실행하면 그 시간 동안
네트워크 스레드가 막혀 keepalive 가 끊긴다. 그래서 `accepted` 를 먼저 보내고 워커에서
돌린 뒤 `progress`/`done` 을 잇는다.

## 알 수 없는 명령도 ack 한다

`error` 상태로 답한다. 무응답이면 클라우드는 "기기가 죽었는지 명령을 무시했는지"를
구별할 수 없다. 안전 기능에서 무증상 침묵은 금지다.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable
from uuid import UUID

from pydantic import ValidationError
from wifiguard_contracts import topics
from wifiguard_contracts.mqtt import AckStatus, CmdMsg

from . import codec
from .publisher import Outgoing

log = logging.getLogger("mqtt.command")

#: action -> 실행 함수. `(CmdMsg, report) -> dict` 를 반환하면 done 의 result 가 된다.
#: `report(status, **kw)` 로 중간 진행을 보낼 수 있다.
CommandHandlerFn = Callable[[CmdMsg, Callable[..., None]], dict[str, Any]]


class CommandHandler:
    def __init__(
        self,
        publisher: Any,
        *,
        device_id: UUID,
        tenant_id: str,
        qos: int = 1,
    ) -> None:
        self.publisher = publisher
        self.device_id = device_id
        self.tenant_id = tenant_id
        self.qos = qos
        self._handlers: dict[str, CommandHandlerFn] = {}
        self._seq = 0
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []

    def register(self, action: str, fn: CommandHandlerFn) -> None:
        self._handlers[action] = fn

    @property
    def cmd_topic(self) -> str:
        return topics.leaf_topic(self.tenant_id, self.device_id, "cmd")

    @property
    def ack_topic(self) -> str:
        return topics.leaf_topic(self.tenant_id, self.device_id, "ack")

    def attach(self, client: Any) -> None:
        """publisher 가 연결됐을 때 호출된다. 재연결마다 다시 구독해야 한다."""
        client.subscribe(self.cmd_topic, qos=self.qos)
        client.on_message = self._on_message
        log.info("cmd 구독: %s", self.cmd_topic)

    # ── 수신 ────────────────────────────────────────────────────────
    def _on_message(self, client, userdata, message) -> None:
        del client, userdata
        try:
            cmd = CmdMsg.model_validate_json(message.payload)
        except ValidationError as exc:
            # cmd_id 를 모르면 ack 를 짝지을 수 없다. 최소한 로그는 남긴다.
            log.warning("cmd 파싱 실패 (%s): %s", message.topic, exc)
            self._ack_unparseable(message.payload, str(exc))
            return

        if str(cmd.device_id) != str(self.device_id):
            # 브로커 ACL 이 뚫렸거나 토픽이 잘못됐다. 남의 명령을 실행하지 않는다.
            log.warning("다른 기기의 cmd 를 받았다: %s", cmd.device_id)
            return

        handler = self._handlers.get(cmd.action)
        if handler is None:
            self._send_ack(cmd.cmd_id, cmd.action, "error", detail=f"지원하지 않는 명령: {cmd.action}")
            return

        self._send_ack(cmd.cmd_id, cmd.action, "accepted")
        worker = threading.Thread(
            target=self._run, args=(cmd, handler), daemon=True, name=f"cmd-{cmd.action}"
        )
        self._workers.append(worker)
        worker.start()

    def _run(self, cmd: CmdMsg, handler: CommandHandlerFn) -> None:
        def report(status: AckStatus = "progress", **kw: Any) -> None:
            self._send_ack(cmd.cmd_id, cmd.action, status, **kw)

        try:
            result = handler(cmd, report) or {}
        except Exception as exc:
            log.exception("cmd %s 실패", cmd.action)
            self._send_ack(cmd.cmd_id, cmd.action, "error", detail=f"{type(exc).__name__}: {exc}")
            return
        self._send_ack(cmd.cmd_id, cmd.action, "done", result=result)

    # ── 발행 ────────────────────────────────────────────────────────
    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _send_ack(self, cmd_id: UUID, action: str, status: AckStatus, **kw: Any) -> None:
        msg = codec.build_ack(
            device_id=self.device_id,
            tenant_id=self.tenant_id,
            seq=self._next_seq(),
            cmd_id=cmd_id,
            action=action,  # type: ignore[arg-type]
            status=status,
            **kw,
        )
        self.publisher.enqueue(
            Outgoing(topic=self.ack_topic, payload=codec.to_bytes(msg), qos=self.qos, kind="ack")
        )

    def _ack_unparseable(self, payload: bytes, detail: str) -> None:
        """파싱이 깨져도 cmd_id 만 건질 수 있으면 error ack 를 보낸다."""
        try:
            cmd_id = UUID(json.loads(payload).get("cmd_id"))
        except Exception:
            return
        self._send_ack(cmd_id, "ping", "error", detail=f"파싱 실패: {detail[:200]}")


def make_ping_handler() -> CommandHandlerFn:
    def _ping(cmd: CmdMsg, report) -> dict[str, Any]:
        del report
        return {"echo": cmd.args}

    return _ping
