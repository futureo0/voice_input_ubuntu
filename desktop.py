from __future__ import annotations

import shutil
import subprocess
import threading
import time
from math import log10

from config import Config


class SystemNotifier:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled and shutil.which("notify-send") is not None
        self.replace_id: str | None = None
        self.lock = threading.Lock()

    def send_once(self, summary: str, body: str, timeout_ms: int = 4000) -> None:
        if not self.enabled:
            return
        self._run(
            [
                "notify-send",
                "-a",
                "Voice Input",
                "-i",
                "audio-input-microphone",
                "-t",
                str(timeout_ms),
                summary,
                body,
            ]
        )

    def replace(self, summary: str, body: str, timeout_ms: int = 0) -> None:
        if not self.enabled:
            return
        with self.lock:
            command = [
                "notify-send",
                "-a",
                "Voice Input",
                "-i",
                "audio-input-microphone",
                "-t",
                str(timeout_ms),
            ]
            if self.replace_id:
                command.extend(["-r", self.replace_id])
            else:
                command.append("-p")
            command.extend([summary, body])
            result = self._run(command)
            notification_id = result.stdout.strip() if result else ""
            if notification_id.isdigit():
                self.replace_id = notification_id

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                command,
                text=True,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None


class SoundPlayer:
    START_EVENT = "device-added"
    STOP_EVENT = "complete"
    START_FILE = "/usr/share/sounds/freedesktop/stereo/device-added.oga"
    STOP_FILE = "/usr/share/sounds/freedesktop/stereo/complete.oga"

    def __init__(self, enabled: bool = True, volume_percent: int = 100):
        self.enabled = enabled
        self.volume_percent = min(200, max(0, volume_percent))

    def recording_started(self) -> None:
        self._play(self.START_EVENT, self.START_FILE, wait=True)

    def recording_stopped(self) -> None:
        self._play(self.STOP_EVENT, self.STOP_FILE, wait=False)

    def _play(self, event_id: str, sound_file: str, *, wait: bool) -> None:
        if not self.enabled:
            return

        if self.volume_percent <= 0:
            return

        command = self._command(event_id, sound_file, self.volume_percent)
        if command is None:
            return

        try:
            if wait:
                subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1,
                )
            else:
                subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except (OSError, subprocess.TimeoutExpired):
            return

    @staticmethod
    def _command(event_id: str, sound_file: str, volume_percent: int) -> list[str] | None:
        if shutil.which("canberra-gtk-play") is not None:
            volume_db = 20 * log10(volume_percent / 100)
            return [
                "canberra-gtk-play",
                "-i",
                event_id,
                "-d",
                "Voice Input",
                "-V",
                f"{volume_db:.2f}",
            ]
        if shutil.which("pw-play") is not None:
            return ["pw-play", "--volume", f"{volume_percent / 100:.2f}", sound_file]
        if shutil.which("aplay") is not None and sound_file.endswith(".wav"):
            return ["aplay", "-q", sound_file]
        return None


class RecordingProgress:
    def __init__(self, notifier: SystemNotifier):
        self.notifier = notifier
        self.started_at = 0.0
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.started_at = time.monotonic()
        self.stop_event.clear()
        self.notifier.replace("语音输入", "录音中 00:00", timeout_ms=0)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop_for_recognition(self) -> None:
        elapsed = self.elapsed_seconds()
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        self.notifier.replace("语音输入", f"录音结束 {format_duration(elapsed)}，正在识别...", timeout_ms=0)

    def elapsed_seconds(self) -> int:
        if self.started_at <= 0:
            return 0
        return max(0, int(time.monotonic() - self.started_at))

    def _run(self) -> None:
        while not self.stop_event.wait(1):
            self.notifier.replace("语音输入", f"录音中 {format_duration(self.elapsed_seconds())}", timeout_ms=0)


class MicrophoneControl:
    SOURCE = "@DEFAULT_AUDIO_SOURCE@"

    def __init__(self, config: Config):
        self.config = config

    def ensure_ready(self) -> bool:
        if not self.config.mic_auto_fix or shutil.which("wpctl") is None:
            return False

        state = self._read_wpctl_state()
        if state is None:
            return False

        volume, muted = state
        min_volume = self.config.mic_min_volume / 100
        if not muted and volume > min_volume:
            return False

        subprocess.run(["wpctl", "set-mute", self.SOURCE, "0"], check=False)
        subprocess.run(
            ["wpctl", "set-volume", self.SOURCE, f"{self.config.mic_target_volume}%"],
            check=False,
        )
        return True

    def _read_wpctl_state(self) -> tuple[float, bool] | None:
        try:
            result = subprocess.run(
                ["wpctl", "get-volume", self.SOURCE],
                text=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError):
            return None

        output = result.stdout.strip()
        muted = "[MUTED]" in output
        for token in output.replace(":", " ").split():
            try:
                return float(token), muted
            except ValueError:
                continue
        return None


def format_duration(seconds: int) -> str:
    minutes, remaining = divmod(seconds, 60)
    return f"{minutes:02d}:{remaining:02d}"
