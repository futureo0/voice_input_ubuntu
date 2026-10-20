from __future__ import annotations

import subprocess
import sys
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
                threading.Thread(
                    target=self._finish_when_done,
                    args=(self.session,),
                    daemon=True,
                ).start()
                return

            session = self.session
            self.session = None

        print("\nStopping...", flush=True)
        self._finish_session(session)

    def _finish_when_done(self, session: VoiceSession) -> None:
        session.done_event.wait()
        with self.lock:
            if self.session is not session:
                return
            self.session = None

        print("\nRecording stopped automatically.", flush=True)
        self._finish_session(session)

    def _finish_session(self, session: VoiceSession) -> None:
        try:
            text = session.stop().strip()
        except Exception as exc:
            error = str(exc)
            print(f"Voice input failed: {error}", file=sys.stderr, flush=True)
            if "not granted" in error or "requested resource not granted" in error:
                message = (
                    "语音识别服务拒绝连接：当前应用没有可用的资源授权。"
                    "请在火山引擎控制台检查服务状态和授权，"
                    "并核对 .env 中的 VOLC_ASR_RESOURCE_ID。"
                )
            else:
                message = f"语音输入失败：{error}"
            self.notifier.replace("语音输入失败", message, timeout_ms=8000)
            return
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
