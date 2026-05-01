from __future__ import annotations

import gzip
import json
import uuid
from typing import Any

import websocket

from config import Config


PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010

NO_SERIALIZATION = 0b0000
JSON_SERIALIZATION = 0b0001

GZIP_COMPRESSION = 0b0001


def make_header(message_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return bytes(
        [
            (PROTOCOL_VERSION << 4) | DEFAULT_HEADER_SIZE,
            (message_type << 4) | flags,
            (serialization << 4) | compression,
            0,
        ]
    )


def pack_client_message(
    message_type: int,
    flags: int,
    payload: bytes,
    serialization: int,
    compression: int,
    sequence: int | None = None,
) -> bytes:
    message = make_header(message_type, flags, serialization, compression)
    if flags & POS_SEQUENCE:
        if sequence is None:
            raise ValueError("sequence is required when sequence flag is set")
        message += sequence.to_bytes(4, "big", signed=True)
    return message + len(payload).to_bytes(4, "big", signed=False) + payload


def parse_server_message(message: bytes) -> tuple[dict[str, Any] | None, bool]:
    if len(message) < 4:
        return None, False

    header_size = message[0] & 0x0F
    message_type = message[1] >> 4
    flags = message[1] & 0x0F
    serialization = message[2] >> 4
    compression = message[2] & 0x0F
    payload = message[header_size * 4 :]
    is_last = bool(flags & NEG_SEQUENCE)
    payload_msg: bytes | None = None
    sequence: int | None = None

    if flags & POS_SEQUENCE:
        if len(payload) < 4:
            return None, is_last
        sequence = int.from_bytes(payload[:4], "big", signed=True)
        payload = payload[4:]
        if sequence < 0:
            is_last = True

    if message_type == SERVER_ERROR_RESPONSE:
        if len(payload) < 8:
            raise RuntimeError(f"ASR server error: {payload.hex()}")
        code = int.from_bytes(payload[:4], "big", signed=False)
        size = int.from_bytes(payload[4:8], "big", signed=False)
        error_payload = payload[8 : 8 + size]
        if compression == GZIP_COMPRESSION and error_payload:
            error_payload = gzip.decompress(error_payload)
        error_text = error_payload.decode("utf-8", errors="replace")
        raise RuntimeError(f"ASR server error {code}: {error_text}")

    if message_type == SERVER_FULL_RESPONSE:
        if len(payload) < 4:
            return None, is_last
        payload_size = int.from_bytes(payload[:4], "big", signed=True)
        payload_msg = payload[4 : 4 + payload_size]
    elif message_type == SERVER_ACK:
        if sequence is None and len(payload) >= 4:
            sequence = int.from_bytes(payload[:4], "big", signed=True)
            payload = payload[4:]
            if sequence < 0:
                is_last = True
        if len(payload) >= 4:
            payload_size = int.from_bytes(payload[:4], "big", signed=False)
            payload_msg = payload[4 : 4 + payload_size]
    else:
        return None, is_last

    if not payload_msg:
        return None, is_last

    if compression == GZIP_COMPRESSION:
        payload_msg = gzip.decompress(payload_msg)
    if serialization != JSON_SERIALIZATION:
        return {"raw": payload_msg.decode("utf-8", errors="replace")}, is_last

    try:
        data = json.loads(payload_msg.decode("utf-8"))
    except json.JSONDecodeError:
        data = {"raw": payload_msg.decode("utf-8", errors="replace")}
    return data, is_last


def extract_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("payload_msg"), dict):
        return extract_text(payload["payload_msg"])

    result = payload.get("result")
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str):
            return text.strip()
        utterances = result.get("utterances")
        if isinstance(utterances, list):
            return "".join(
                item.get("text", "")
                for item in utterances
                if isinstance(item, dict) and item.get("definite", True)
            ).strip()
    if isinstance(result, list):
        parts: list[str] = []
        for item in result:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    text = payload.get("text")
    return text.strip() if isinstance(text, str) else ""


class DoubaoAsrClient:
    def __init__(self, config: Config):
        self.config = config
        self.ws: websocket.WebSocket | None = None

    def connect(self) -> None:
        connect_id = str(uuid.uuid4())
        headers = [
            f"X-Api-App-Key: {self.config.app_key}",
            f"X-Api-Access-Key: {self.config.access_key}",
            f"X-Api-Resource-Id: {self.config.resource_id}",
            f"X-Api-Connect-Id: {connect_id}",
        ]
        try:
            self.ws = websocket.create_connection(
                self.config.endpoint,
                header=headers,
                timeout=10,
                enable_multithread=True,
            )
        except websocket.WebSocketBadStatusException as exc:
            raise RuntimeError(self._format_handshake_error(exc)) from exc
        self.ws.settimeout(1)

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None

    def send_initial_request(self) -> None:
        request = {
            "user": {"uid": self.config.uid},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": self.config.sample_rate,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "result_type": "full",
                "enable_punc": self.config.enable_punc,
                "enable_itn": self.config.enable_itn,
                "show_utterances": self.config.show_utterances,
            },
        }
        payload = gzip.compress(json.dumps(request).encode("utf-8"))
        self._send_binary(
            pack_client_message(
                CLIENT_FULL_REQUEST,
                NO_SEQUENCE,
                payload,
                JSON_SERIALIZATION,
                GZIP_COMPRESSION,
            )
        )

    def send_audio(self, pcm: bytes, last: bool = False) -> None:
        payload = gzip.compress(pcm)
        self._send_binary(
            pack_client_message(
                CLIENT_AUDIO_ONLY_REQUEST,
                NEG_SEQUENCE if last else NO_SEQUENCE,
                payload,
                NO_SERIALIZATION,
                GZIP_COMPRESSION,
            )
        )

    def receive(self) -> tuple[dict[str, Any] | None, bool] | None:
        if self.ws is None:
            return None
        try:
            frame = self.ws.recv()
        except websocket.WebSocketTimeoutException:
            return None
        if frame == "":
            return None
        if isinstance(frame, str):
            return {"raw": frame}, False
        return parse_server_message(frame)

    def _send_binary(self, data: bytes) -> None:
        if self.ws is None:
            raise RuntimeError("WebSocket is not connected")
        self.ws.send_binary(data)

    def _format_handshake_error(self, exc: websocket.WebSocketBadStatusException) -> str:
        body = exc.resp_body
        if isinstance(body, bytes):
            body_text = body.decode("utf-8", errors="replace")
        else:
            body_text = str(body or "")

        if exc.status_code == 403 and "requested resource not granted" in body_text:
            return (
                "ASR handshake failed: current VolcEngine app is not granted for "
                f"VOLC_ASR_RESOURCE_ID={self.config.resource_id!r}. "
                "Open the matching Doubao streaming ASR resource in the VolcEngine console, "
                "or change VOLC_ASR_RESOURCE_ID in .env to the resource id your app has. "
                "Common ids: volc.bigasr.sauc.duration, volc.bigasr.sauc.concurrent, "
                "volc.seedasr.sauc.duration, volc.seedasr.sauc.concurrent."
            )

        return f"ASR handshake failed with HTTP {exc.status_code}: {body_text or exc}"
