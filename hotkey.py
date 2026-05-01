from __future__ import annotations

import selectors
import sys
import time
from collections.abc import Callable

from evdev import InputDevice, UInput, UInputError, ecodes, list_devices


class AltTapListener:
    RESCAN_SECONDS = 5.0
    ALT_KEYS = {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT}
    HWHEEL_CODES = tuple(
        code
        for code in (
            ecodes.REL_HWHEEL,
            getattr(ecodes, "REL_HWHEEL_HI_RES", None),
        )
        if code is not None
    )

    def __init__(
        self,
        on_alt_tap: Callable[[], None],
        debounce_ms: int,
        *,
        m585_wheel_enabled: bool = True,
        m585_device_names: tuple[str, ...] = ("M585", "M590"),
        m585_left_sign: int = -1,
        m585_intercept: bool = True,
        m585_gesture_ms: int = 1200,
    ):
        self.on_alt_tap = on_alt_tap
        self.debounce_seconds = max(0, debounce_ms) / 1000
        self.m585_gesture_seconds = max(0, m585_gesture_ms) / 1000
        self.selector = selectors.DefaultSelector()
        self.devices: dict[int, InputDevice] = {}
        self.alt_down: dict[tuple[int, int], bool] = {}
        self.alt_clean: dict[tuple[int, int], bool] = {}
        self.m585_wheel_enabled = m585_wheel_enabled
        self.m585_device_names = tuple(name.lower() for name in m585_device_names)
        self.m585_left_sign = -1 if m585_left_sign < 0 else 1
        self.m585_intercept = m585_intercept
        self.m585_wheel_fds: set[int] = set()
        self.proxies: dict[int, UInput] = {}
        self.packet_buffers: dict[int, list] = {}
        self.m585_wheel_block_until: dict[int, float] = {}
        self.device_paths: dict[int, str] = {}
        self.permission_denied_paths: set[str] = set()
        self.last_tap_at = 0.0

    def open_devices(self, *, require_any: bool = True) -> int:
        opened = 0
        paths = list(list_devices())
        self._close_missing_devices(paths)

        for path in paths:
            if path in self.device_paths.values():
                continue
            try:
                device = InputDevice(path)
                self.permission_denied_paths.discard(path)
                capabilities = device.capabilities().get(ecodes.EV_KEY, [])
                listens_for_alt = any(code in capabilities for code in self.ALT_KEYS)
                listens_for_m585 = self._is_m585_wheel_device(device)
                if listens_for_alt or listens_for_m585:
                    self.selector.register(device.fd, selectors.EVENT_READ, device)
                    self.devices[device.fd] = device
                    self.device_paths[device.fd] = device.path
                    opened += 1
                    labels: list[str] = []
                    if listens_for_alt:
                        labels.append("keyboard")
                    if listens_for_m585:
                        self.m585_wheel_fds.add(device.fd)
                        labels.append("M585 wheel")
                    intercept_mode = ""
                    if listens_for_m585:
                        intercept_mode = self._enable_m585_intercept(device)
                    print(f"Listening {' + '.join(labels)}: {device.path} ({device.name}){intercept_mode}")
                else:
                    device.close()
            except PermissionError:
                if path not in self.permission_denied_paths:
                    self.permission_denied_paths.add(path)
                    print(f"Permission denied: {path}", file=sys.stderr)
            except OSError:
                continue

        if require_any and not self.devices:
            raise SystemExit(
                "No readable keyboard or M585 wheel devices found. Add the user to the input group "
                "or run this script with sudo."
            )
        return opened

    def run(self) -> None:
        self.open_devices()
        trigger_names = ["Alt"]
        if self.m585_wheel_fds:
            trigger_names.append("M585 wheel-left")
        print(f"Ready. Tap {' or '.join(trigger_names)} to start/stop recording.", flush=True)
        try:
            while True:
                for key, _ in self.selector.select(timeout=self.RESCAN_SECONDS):
                    device: InputDevice = key.data
                    try:
                        for event in device.read():
                            self._handle_event(device, event)
                    except OSError as exc:
                        print(f"Input device disconnected: {device.path} ({device.name}): {exc}", file=sys.stderr)
                        self._close_device(device)
                self.open_devices(require_any=False)
        finally:
            self.close()

    def _handle_event(self, device: InputDevice, event) -> None:
        if device.fd in self.proxies:
            self._handle_proxied_event(device, event)
            return

        if self._is_m585_left_wheel_event(device, event):
            self._trigger_m585_wheel(device)
            return

        self._handle_alt_event(device, event)

    def _handle_alt_event(self, device: InputDevice, event) -> None:
        if event.type != ecodes.EV_KEY:
            return
        key_id = (device.fd, event.code)
        if event.code in self.ALT_KEYS:
            if event.value == 1:
                self.alt_down[key_id] = True
                self.alt_clean[key_id] = True
            elif event.value == 0 and self.alt_down.pop(key_id, False):
                clean = self.alt_clean.pop(key_id, False)
                if clean:
                    self._trigger()
            return

        if event.value in {1, 2}:
            for alt_key in list(self.alt_down):
                self.alt_clean[alt_key] = False

    def _handle_proxied_event(self, device: InputDevice, event) -> None:
        buffer = self.packet_buffers.setdefault(device.fd, [])
        buffer.append(event)
        if event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
            packet = buffer[:]
            buffer.clear()
            self._handle_proxied_packet(device, packet)

    def _handle_proxied_packet(self, device: InputDevice, packet: list) -> None:
        drop_left_wheel = any(self._is_m585_left_wheel_event(device, event) for event in packet)
        if drop_left_wheel:
            self._trigger_m585_wheel(device)

        for event in packet:
            self._handle_alt_event(device, event)

        proxy = self.proxies.get(device.fd)
        if proxy is None:
            return

        try:
            for event in packet:
                if drop_left_wheel and self._is_m585_left_wheel_event(device, event):
                    continue
                if event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                    proxy.syn()
                else:
                    proxy.write_event(event)
        except (OSError, UInputError) as exc:
            print(f"Lost M585 uinput proxy for {device.path}: {exc}", file=sys.stderr)
            self._close_device(device)

    def _trigger(self) -> None:
        now = time.monotonic()
        if now - self.last_tap_at >= self.debounce_seconds:
            self.last_tap_at = now
            self.on_alt_tap()

    def _trigger_m585_wheel(self, device: InputDevice) -> None:
        now = time.monotonic()
        block_until = self.m585_wheel_block_until.get(device.fd, 0.0)
        self.m585_wheel_block_until[device.fd] = now + self.m585_gesture_seconds
        if now >= block_until and now - self.last_tap_at >= self.debounce_seconds:
            self.last_tap_at = now
            self.on_alt_tap()

    def _is_m585_wheel_device(self, device: InputDevice) -> bool:
        if not self.m585_wheel_enabled:
            return False
        device_name = (device.name or "").lower()
        if not any(name in device_name for name in self.m585_device_names):
            return False
        rel_codes = device.capabilities().get(ecodes.EV_REL, [])
        return any(code in rel_codes for code in self.HWHEEL_CODES)

    def _is_m585_left_wheel_event(self, device: InputDevice, event) -> bool:
        return (
            device.fd in self.m585_wheel_fds
            and event.type == ecodes.EV_REL
            and event.code in self.HWHEEL_CODES
            and event.value * self.m585_left_sign > 0
        )

    def _enable_m585_intercept(self, device: InputDevice) -> str:
        if not self.m585_intercept:
            return " (passive)"

        proxy: UInput | None = None
        try:
            proxy = UInput.from_device(
                device,
                name=f"voice-input proxy {device.name}",
                phys="voice-input/uinput",
            )
            device.grab()
        except (OSError, UInputError) as exc:
            if proxy is not None:
                proxy.close()
            print(
                f"Could not intercept {device.path} ({device.name}); listening passively instead: {exc}",
                file=sys.stderr,
            )
            return " (passive; intercept unavailable)"

        self.proxies[device.fd] = proxy
        self.packet_buffers[device.fd] = []
        return " (intercepting left wheel)"

    def close(self) -> None:
        for device in list(self.devices.values()):
            self._close_device(device)

    def _close_missing_devices(self, current_paths: list[str]) -> None:
        current_path_set = set(current_paths)
        for fd, path in list(self.device_paths.items()):
            if path not in current_path_set:
                device = self.devices.get(fd)
                if device is not None:
                    print(f"Input device removed: {device.path} ({device.name})", file=sys.stderr)
                    self._close_device(device)

    def _close_device(self, device: InputDevice) -> None:
        fd = device.fd
        try:
            self.selector.unregister(fd)
        except Exception:
            pass
        proxy = self.proxies.pop(fd, None)
        if proxy is not None:
            try:
                device.ungrab()
            except OSError:
                pass
            proxy.close()
        self.packet_buffers.pop(fd, None)
        self.m585_wheel_block_until.pop(fd, None)
        self.m585_wheel_fds.discard(fd)
        self.devices.pop(fd, None)
        self.device_paths.pop(fd, None)
        device.close()
