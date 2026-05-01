from __future__ import annotations

import subprocess
import threading

from clipboard import Clipboard
from config import Config
from desktop import MicrophoneControl, SoundPlayer, SystemNotifier
from session import VoiceSession


class VoiceInputController:
    def __init__(self, config: Config):
        self.config = config
        self.notifier = SystemNotifier(config.notifications)
        self.sounds = SoundPlayer(config.sounds, config.sound_volume)
        self.session: VoiceSession | None = None
        self.lock = threading.Lock()

    def toggle(self) -> None:
        with self.lock:
            if self.session is None:
                if MicrophoneControl(self.config).ensure_ready():
                    message = (
                        "当前麦克风关闭，已为您打开"
                        f"并将音量设置为{self.config.mic_target_volume}%"
                    )
                    print(message, flush=True)
                    self.notifier.send_once("语音输入", message, timeout_ms=4000)
                self.session = VoiceSession(self.config, self.notifier, self.sounds)
                self.session.start()
                return

            session = self.session
            self.session = None

        print("\nStopping...", flush=True)
        text = session.stop().strip()
        if not text:
            print("No recognized text. Run with VOICE_INPUT_DEBUG=1 to print ASR responses.", flush=True)
            self.notifier.replace("语音输入", "没有识别到文本", timeout_ms=3000)
            return

        clipboard_result = Clipboard.copy(text, self.config.copyq_history)
        paste_result = None
        paste_error = None
        if self.config.auto_paste:
            try:
                paste_result = Clipboard.paste_from_copyq_latest(self.config.paste_delay_ms)
            except subprocess.CalledProcessError as exc:
                paste_error = exc.stderr.strip() if exc.stderr else str(exc)

        if paste_result:
            print(
                f"\nCopied via {clipboard_result.command} ({clipboard_result.detail}), "
                f"pasted via {paste_result}: {text}",
                flush=True,
            )
            self.notifier.replace("语音输入", f"已复制并粘贴：{text}", timeout_ms=3500)
        elif paste_error:
            print(
                f"\nCopied via {clipboard_result.command} ({clipboard_result.detail}), "
                f"paste failed: {paste_error}\n{text}",
                flush=True,
            )
            self.notifier.replace("语音输入", f"已复制，自动粘贴失败：{paste_error}", timeout_ms=5000)
        else:
            print(f"\nCopied via {clipboard_result.command} ({clipboard_result.detail}): {text}", flush=True)
            self.notifier.replace("语音输入", f"已复制：{text}", timeout_ms=3500)
