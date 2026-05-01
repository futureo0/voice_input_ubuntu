from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ClipboardResult:
    command: str
    detail: str = ""


class Clipboard:
    @staticmethod
    def copy(text: str, add_to_copyq_history: bool = True) -> ClipboardResult:
        errors: list[str] = []

        if shutil.which("copyq") is not None:
            try:
                if add_to_copyq_history:
                    Clipboard._run(["copyq", "add", "-"], text)
                    Clipboard._run(["copyq", "select", "0"])
                    return ClipboardResult("copyq", "add/select")
                Clipboard._run(["copyq", "copy", "-"], text)
                return ClipboardResult("copyq", "copy")
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.strip() if exc.stderr else str(exc)
                errors.append(f"copyq: {stderr}")

        commands = [
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]
        for command in commands:
            if shutil.which(command[0]) is None:
                continue
            try:
                Clipboard._run(command, text)
                return ClipboardResult(command[0])
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.strip() if exc.stderr else str(exc)
                errors.append(f"{command[0]}: {stderr}")

        details = "; ".join(errors) if errors else "no clipboard command found"
        raise RuntimeError(f"Could not copy text to clipboard: {details}")

    @staticmethod
    def paste_from_copyq_latest(delay_ms: int = 0) -> str | None:
        if shutil.which("copyq") is None:
            return None
        if delay_ms > 0:
            time.sleep(delay_ms / 1000)
        Clipboard._run(["copyq", "select", "0"])
        Clipboard._run(["copyq", "paste"])
        return "copyq select 0 + paste"

    @staticmethod
    def _run(command: list[str], input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            input=input_text,
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
