#!/usr/bin/python3
"""Restart this folder's assistant, including any stuck recording children."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import signal
import subprocess
import time
from datetime import datetime


APP_DIR = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "voice-input"


def notify(message: str) -> None:
    try:
        subprocess.run(["notify-send", "语音输入", message], timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def assistant_processes() -> set[int]:
    """Match the script argument exactly; leave other Python programs alone."""
    parents: dict[int, int] = {}
    targets: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            pid = int(entry.name)
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            parents[pid] = int(stat[1])
            args = (entry / "cmdline").read_bytes().split(b"\0")
            if len(args) > 1 and args[1] == os.fsencode(APP_DIR / "voice_input.py"):
                targets.add(pid)
            elif len(args) > 1 and args[1] == b"voice_input.py":
                if (entry / "cwd").resolve() == APP_DIR:
                    targets.add(pid)
        except (OSError, ValueError, IndexError):
            continue
    while True:
        children = {pid for pid, parent in parents.items() if parent in targets}
        if children <= targets:
            return targets
        targets.update(children)


def send_signal(pids: set[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "restart.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        with (STATE_DIR / "voice_input.log").open("ab", buffering=0) as log:
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            log.write(f"\n[{stamp}] Restart requested\n".encode())
            targets = assistant_processes()
            send_signal(targets, signal.SIGTERM)
            if targets:
                time.sleep(1)
                send_signal(targets, signal.SIGKILL)
                time.sleep(0.2)
            offset = log.tell()
            process = subprocess.Popen(
                [str(APP_DIR / "run_voice_input.sh")],
                cwd=APP_DIR,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                with (STATE_DIR / "voice_input.log").open("rb") as reader:
                    reader.seek(offset)
                    if b"Ready. Tap" in reader.read():
                        notify("已重启，可以按 Alt 或左拨滚轮开始录音")
                        return 0
                if process.poll() is not None:
                    break
                time.sleep(0.2)
            notify("重启后尚未就绪，请查看 voice_input.log 日志")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
