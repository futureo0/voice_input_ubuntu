#!/usr/bin/env python3
from __future__ import annotations

import sys

from config import Config
from controller import VoiceInputController
from hotkey import AltTapListener


def main() -> int:
    try:
        config = Config.from_env()
        controller = VoiceInputController(config)
        listener = AltTapListener(
            controller.toggle,
            config.alt_debounce_ms,
            m585_wheel_enabled=config.m585_wheel_enabled,
            m585_device_names=config.m585_device_names,
            m585_left_sign=config.m585_left_sign,
            m585_intercept=config.m585_intercept,
            m585_gesture_ms=config.m585_gesture_ms,
        )
        listener.run()
    except KeyboardInterrupt:
        print("\nBye.")
        return 0
    except BaseException as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
