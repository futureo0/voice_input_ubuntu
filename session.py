from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading

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
            while True:
                chunk = recorder.stdout.read(bytes_per_chunk) if recorder.stdout else b""
                if not chunk:
                    break
                last = self.stop_event.is_set()
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
