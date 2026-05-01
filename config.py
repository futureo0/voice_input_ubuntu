from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise SystemExit(f"{name} must be an integer, got: {value!r}") from None


def env_int_range(name: str, default: int, minimum: int, maximum: int) -> int:
    value = env_int(name, default)
    if not minimum <= value <= maximum:
        raise SystemExit(f"{name} must be between {minimum} and {maximum}, got: {value}")
    return value


def env_csv(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(part.strip() for part in value.split(",") if part.strip())


def env_sign(name: str, default: int) -> int:
    value = env_int(name, default)
    if value == 0:
        raise SystemExit(f"{name} must be positive or negative, got: 0")
    return -1 if value < 0 else 1


@dataclass(frozen=True)
class Config:
    app_key: str
    access_key: str
    resource_id: str
    endpoint: str
    uid: str
    audio_device: str
    sample_rate: int
    chunk_ms: int
    final_timeout: int
    enable_punc: bool
    enable_itn: bool
    show_utterances: bool
    debug: bool
    notifications: bool
    sounds: bool
    sound_volume: int
    mic_auto_fix: bool
    mic_target_volume: int
    mic_min_volume: int
    copyq_history: bool
    auto_paste: bool
    paste_delay_ms: int
    alt_debounce_ms: int
    m585_wheel_enabled: bool
    m585_device_names: tuple[str, ...]
    m585_left_sign: int
    m585_intercept: bool
    m585_gesture_ms: int

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(Path(".env"))
        app_key = os.getenv("VOLC_ASR_APP_KEY", "").strip()
        access_key = os.getenv("VOLC_ASR_ACCESS_KEY", "").strip()
        if not app_key or not access_key:
            raise SystemExit(
                "Missing VOLC_ASR_APP_KEY or VOLC_ASR_ACCESS_KEY. "
                "Copy .env.example to .env and fill in your credentials."
            )

        return cls(
            app_key=app_key,
            access_key=access_key,
            resource_id=os.getenv("VOLC_ASR_RESOURCE_ID", "volc.bigasr.sauc.duration").strip(),
            endpoint=os.getenv(
                "VOLC_ASR_ENDPOINT",
                "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
            ).strip(),
            uid=os.getenv("VOLC_ASR_UID", "voice-input").strip(),
            audio_device=os.getenv("VOICE_INPUT_AUDIO_DEVICE", "default").strip(),
            sample_rate=env_int("VOICE_INPUT_SAMPLE_RATE", 16000),
            chunk_ms=env_int("VOICE_INPUT_CHUNK_MS", 200),
            final_timeout=env_int("VOICE_INPUT_FINAL_TIMEOUT", 12),
            enable_punc=env_bool("VOLC_ASR_ENABLE_PUNC", True),
            enable_itn=env_bool("VOLC_ASR_ENABLE_ITN", True),
            show_utterances=env_bool("VOLC_ASR_SHOW_UTTERANCES", True),
            debug=env_bool("VOICE_INPUT_DEBUG", False),
            notifications=env_bool("VOICE_INPUT_NOTIFICATIONS", True),
            sounds=env_bool("VOICE_INPUT_SOUNDS", True),
            sound_volume=env_int_range("VOICE_INPUT_SOUND_VOLUME", 100, 0, 200),
            mic_auto_fix=env_bool("VOICE_INPUT_MIC_AUTO_FIX", True),
            mic_target_volume=env_int("VOICE_INPUT_MIC_TARGET_VOLUME", 30),
            mic_min_volume=env_int("VOICE_INPUT_MIC_MIN_VOLUME", 2),
            copyq_history=env_bool("VOICE_INPUT_COPYQ_HISTORY", True),
            auto_paste=env_bool("VOICE_INPUT_AUTO_PASTE", True),
            paste_delay_ms=env_int("VOICE_INPUT_PASTE_DELAY_MS", 0),
            alt_debounce_ms=env_int("VOICE_INPUT_ALT_DEBOUNCE_MS", 350),
            m585_wheel_enabled=env_bool("VOICE_INPUT_M585_WHEEL", True),
            m585_device_names=env_csv("VOICE_INPUT_M585_DEVICE_NAMES", "M585,M590"),
            m585_left_sign=env_sign("VOICE_INPUT_M585_LEFT_SIGN", -1),
            m585_intercept=env_bool("VOICE_INPUT_M585_INTERCEPT", True),
            m585_gesture_ms=env_int("VOICE_INPUT_M585_GESTURE_MS", 1200),
        )
