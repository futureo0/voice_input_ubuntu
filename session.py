from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

from asr import DoubaoAsrClient, extract_text
from config import Config
from desktop import RecordingProgress, SoundPlayer, SystemNotifier


class VoiceSession:
    def __init__(self, config: Config, notifier: SystemNotifier, sounds: SoundPlayer):
        self.config = config
        self.notifier = notifier
        self.sounds = sounds
        self.progress = RecordingProgress(notifier)
        self.stop_event = threading.Event()
        self.done_event = threading.Event()
        self.final_response_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.latest_text = ""
        self.error: BaseException | None = None
        self.sent_chunks = 0
        self.sent_bytes = 0
        self.received_payloads = 0
        self.received_final = False
        self.last_text_at = 0.0

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> str:
        self.stop_event.set()
        self.thread.join(timeout=self.config.final_timeout + 5)
        if self.thread.is_alive():
            raise RuntimeError("voice session did not stop in time")
        if self.error:
            raise self.error
        return self.latest_text

    def _run(self) -> None:
        recorder: subprocess.Popen[bytes] | None = None
        client = DoubaoAsrClient(self.config)
        receiver = threading.Thread(target=self._receive_loop, args=(client,), daemon=True)
        try:
            client.connect()
            client.send_initial_request()
            receiver.start()

            self.sounds.recording_started()
            recorder = self._start_recorder()
            bytes_per_chunk = int(self.config.sample_rate * 2 * self.config.chunk_ms / 1000)
            print("Recording...", flush=True)
            self.progress.start()

            sent_any_audio = False
            started_at = time.monotonic()
            last_voice_at = started_at
            next_reminder_at = started_at + self.config.recording_reminder_seconds
            voice_activity = VoiceActivityDetector(self.config)
            while True:
                chunk = recorder.stdout.read(bytes_per_chunk) if recorder.stdout else b""
                if not chunk:
                    break
                now = time.monotonic()
                if voice_activity.is_voice(chunk) or self.last_text_at > last_voice_at:
                    last_voice_at = max(now, self.last_text_at)
                if self._should_play_reminder(now, next_reminder_at):
                    self.sounds.recording_reminder()
                    next_reminder_at += self.config.recording_reminder_seconds
                silent_too_long = self._silent_too_long(now, last_voice_at)
                if silent_too_long:
                    timeout = self.config.silence_timeout_seconds
                    print(f"\nStopping after {timeout}s without voice input...", flush=True)
                    self.notifier.replace(
                        "语音输入",
                        f"超过 {timeout} 秒没有检测到语音活动，已自动停止录音",
                        timeout_ms=3000,
                    )
                last = self.stop_event.is_set() or silent_too_long
                client.send_audio(chunk, last=last)
                sent_any_audio = True
                self.sent_chunks += 1
                self.sent_bytes += len(chunk)
                if last:
                    break

            if not sent_any_audio and recorder.poll() not in {None, 0}:
                stderr = recorder.stderr.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"arecord failed: {stderr or recorder.returncode}")

            if not sent_any_audio:
                client.send_audio(b"", last=True)

            self._stop_recorder(recorder)
            recorder = None
            self.sounds.recording_stopped()
            self.progress.stop_for_recognition()
            self.final_response_event.wait(self.config.final_timeout)
            if self.config.debug:
                print(
                    "\nDEBUG session: "
                    f"sent_chunks={self.sent_chunks}, sent_bytes={self.sent_bytes}, "
                    f"received_payloads={self.received_payloads}, "
                    f"received_final={self.received_final}",
                    file=sys.stderr,
                    flush=True,
                )
        except BaseException as exc:
            self.error = exc
        finally:
            self.progress.stop_event.set()
            if self.progress.thread:
                self.progress.thread.join(timeout=1)
            if recorder is not None:
                self._stop_recorder(recorder)
            self.done_event.set()
            client.close()

    def _receive_loop(self, client: DoubaoAsrClient) -> None:
        while not self.done_event.is_set():
            try:
                received = client.receive()
            except BaseException as exc:
                if self.done_event.is_set():
                    return
                if self.error is None:
                    self.error = exc
                self.final_response_event.set()
                return
            if received is None:
                continue
            payload, is_last = received
            if payload:
                self.received_payloads += 1
                if self.config.debug:
                    debug_payload = json.dumps(payload, ensure_ascii=False)
                    print(f"\nDEBUG ASR payload: {debug_payload[:2000]}", file=sys.stderr, flush=True)
                text = extract_text(payload)
                if text:
                    self.latest_text = text
                    self.last_text_at = time.monotonic()
                    print(f"\rASR: {text}", end="", flush=True)
            if is_last:
                self.received_final = True
                self.final_response_event.set()
                return

    def _start_recorder(self) -> subprocess.Popen[bytes]:
        command = [
            "arecord",
            "-q",
            "-D",
            self.config.audio_device,
            "-f",
            "S16_LE",
            "-r",
            str(self.config.sample_rate),
            "-c",
            "1",
            "-t",
            "raw",
        ]
        try:
            return subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError:
            raise RuntimeError("arecord not found. Install alsa-utils first.") from None

    def _should_play_reminder(self, now: float, next_reminder_at: float) -> bool:
        return self.config.recording_reminder_seconds > 0 and now >= next_reminder_at

    def _silent_too_long(self, now: float, last_voice_at: float) -> bool:
        return (
            self.config.silence_timeout_seconds > 0
            and now - last_voice_at >= self.config.silence_timeout_seconds
        )

    @staticmethod
    def _stop_recorder(recorder: subprocess.Popen[bytes]) -> None:
        if recorder.poll() is not None:
            return
        try:
            os.killpg(recorder.pid, signal.SIGTERM)
            recorder.wait(timeout=1)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            os.killpg(recorder.pid, signal.SIGKILL)
            recorder.wait(timeout=1)


@dataclass
class AudioStats:
    rms: float
    peak: int


class VoiceActivityDetector:
    def __init__(self, config: Config):
        self.config = config
        self.frame_ms = 20
        self.bytes_per_frame = int(config.sample_rate * 2 * self.frame_ms / 1000)
        self.webrtc_vad = self._create_webrtc_vad()
        self.energy_vad = AdaptiveEnergyVad(config.vad_min_rms)
        self.active_ms = 0
        self.quiet_ms = 0
        self.in_voice = False

    def is_voice(self, chunk: bytes) -> bool:
        raw_voice = self._raw_is_voice(chunk)
        chunk_ms = max(1, int(len(chunk) / max(1, self.config.sample_rate * 2) * 1000))

        if raw_voice:
            self.active_ms += chunk_ms
            self.quiet_ms = 0
        else:
            self.quiet_ms += chunk_ms
            self.active_ms = max(0, self.active_ms - chunk_ms)

        if self.active_ms >= 300:
            self.in_voice = True
        elif self.quiet_ms >= 700:
            self.in_voice = False

        return self.in_voice

    def _raw_is_voice(self, chunk: bytes) -> bool:
        if self.webrtc_vad is not None and self.bytes_per_frame > 0:
            voiced_frames = 0
            total_frames = 0
            for index in range(0, len(chunk) - self.bytes_per_frame + 1, self.bytes_per_frame):
                frame = chunk[index : index + self.bytes_per_frame]
                total_frames += 1
                try:
                    if self.webrtc_vad.is_speech(frame, self.config.sample_rate):
                        voiced_frames += 1
                except Exception:
                    self.webrtc_vad = None
                    break
            if self.webrtc_vad is not None and total_frames:
                return voiced_frames >= max(2, total_frames // 4)

        return self.energy_vad.is_voice(chunk)

    def _create_webrtc_vad(self):
        if self.config.sample_rate not in {8000, 16000, 32000, 48000}:
            return None
        try:
            import webrtcvad
        except ImportError:
            return None
        return webrtcvad.Vad(self.config.vad_aggressiveness)


class AdaptiveEnergyVad:
    def __init__(self, min_rms: int):
        self.min_rms = max(1, min_rms)
        self.noise_floor = float(self.min_rms)
        self.recent_rms: deque[float] = deque(maxlen=25)

    def is_voice(self, chunk: bytes) -> bool:
        stats = self._stats(chunk)
        if stats is None:
            return False

        self.recent_rms.append(stats.rms)
        local_floor = min(self.recent_rms) if self.recent_rms else stats.rms
        self.noise_floor = min(self.noise_floor, local_floor) if self.noise_floor else local_floor
        threshold = max(float(self.min_rms), self.noise_floor * 2.8, self.noise_floor + 120)
        voice = stats.rms >= threshold and stats.peak >= threshold * 1.8

        if not voice:
            rate = 0.06 if stats.rms > self.noise_floor else 0.2
            self.noise_floor = self.noise_floor * (1 - rate) + stats.rms * rate

        return voice

    @staticmethod
    def _stats(chunk: bytes) -> AudioStats | None:
        if len(chunk) < 2:
            return None

        total = 0
        peak = 0
        samples = 0
        for index in range(0, len(chunk) - 1, 2):
            sample = int.from_bytes(chunk[index : index + 2], byteorder="little", signed=True)
            abs_sample = abs(sample)
            total += sample * sample
            peak = max(peak, abs_sample)
            samples += 1
        if samples == 0:
            return None

        return AudioStats(rms=(total / samples) ** 0.5, peak=peak)
