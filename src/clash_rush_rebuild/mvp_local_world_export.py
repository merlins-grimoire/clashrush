"""Memory-only account verification through Clash world export."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from ctypes import wintypes
from typing import Callable, Protocol

from .input_authorization import InputAction

SETTINGS_BUTTON = (0.9547, 0.7271)
MORE_SETTINGS_BUTTON = (0.5110, 0.8340)
EXPORT_BUTTON = (0.7058, 0.6288)
SCROLL_DRAG = (0.50, 0.74, 0.50, 0.24)
MAX_BYTES = 2_000_000
MAX_EXPORT_AGE_SECONDS = 120
MAX_FUTURE_SKEW_SECONDS = 30
CF_UNICODETEXT = 13
_HEX_64 = re.compile(r"[0-9a-f]{64}")


class ExportCaptureError(RuntimeError):
    """Fresh private account evidence could not be established."""


@dataclass(frozen=True, slots=True)
class WorldExportSummary:
    exported_at: datetime
    account_matches: bool


class ExportInputPort(Protocol):
    def click(
        self,
        binding: object,
        x: float,
        y: float,
        *,
        action: InputAction,
    ) -> bool: ...

    def drag(
        self,
        binding: object,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        action: InputAction,
    ) -> bool: ...


def _configure_clipboard_api(user32: object, kernel32: object) -> None:
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL


def _open_clipboard(user32: object) -> None:
    for _ in range(20):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.025)
    raise ExportCaptureError("Windows clipboard is unavailable")


def read_windows_clipboard() -> str:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _configure_clipboard_api(user32, kernel32)
    _open_clipboard(user32)
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ExportCaptureError("clipboard text could not be locked")
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clear_windows_clipboard() -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _configure_clipboard_api(user32, kernel32)
    _open_clipboard(user32)
    try:
        if not user32.EmptyClipboard():
            raise ExportCaptureError("clipboard could not be cleared")
    finally:
        user32.CloseClipboard()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ExportCaptureError("duplicate JSON key")
        result[key] = value
    return result


def summarize_world_export(
    text: str,
    *,
    expected_tag_sha256: str,
    now: datetime | None = None,
) -> WorldExportSummary:
    if type(text) is not str or not text.strip() or len(text.encode("utf-8")) > MAX_BYTES:
        raise ExportCaptureError("clipboard export is malformed")
    if type(expected_tag_sha256) is not str or _HEX_64.fullmatch(expected_tag_sha256) is None:
        raise ExportCaptureError("configured account hash is malformed")
    try:
        raw = json.loads(text, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ExportCaptureError("clipboard export is malformed") from exc
    if type(raw) is not dict:
        raise ExportCaptureError("clipboard export is malformed")
    tag = raw.get("tag")
    timestamp = raw.get("timestamp")
    if type(tag) is not str or not tag or type(timestamp) is not int or timestamp < 0:
        raise ExportCaptureError("clipboard export is malformed")
    observed = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(observed, expected_tag_sha256):
        raise ExportCaptureError("export account binding mismatch")
    clock = now or datetime.now(UTC)
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise ExportCaptureError("freshness clock is malformed")
    exported = datetime.fromtimestamp(timestamp, UTC)
    age = (clock.astimezone(UTC) - exported).total_seconds()
    if age > MAX_EXPORT_AGE_SECONDS:
        raise ExportCaptureError("export is stale")
    if age < -MAX_FUTURE_SKEW_SECONDS:
        raise ExportCaptureError("export timestamp is in the future")
    tag = ""
    raw.clear()
    return WorldExportSummary(exported, True)


def capture_world_export(
    binding: object,
    *,
    input_port: ExportInputPort,
    expected_tag_sha256: str,
    live_gate: Callable[[], bool],
    clipboard_read: Callable[[], str],
    clipboard_clear: Callable[[], None],
    sleep: Callable[[float], None],
    scroll_drags: int = 3,
    now: datetime | None = None,
) -> WorldExportSummary:
    if type(scroll_drags) is not int or not 0 <= scroll_drags <= 5:
        raise ExportCaptureError("scroll count is malformed")
    previous = ""
    exported = ""
    try:
        previous = clipboard_read()
        clipboard_clear()
        operations = [
            ("click", SETTINGS_BUTTON, 1.0),
            ("click", MORE_SETTINGS_BUTTON, 1.0),
            *(("drag", SCROLL_DRAG, 0.15) for _ in range(scroll_drags)),
            ("click", EXPORT_BUTTON, 0.75),
        ]
        for kind, points, delay in operations:
            try:
                authorized = live_gate()
            except BaseException as exc:
                raise ExportCaptureError("account verification live gate unavailable") from exc
            if authorized is not True:
                raise ExportCaptureError("account verification live gate disabled")
            try:
                delivered = (
                    input_port.click(
                        binding,
                        *points,
                        action=InputAction.ACCOUNT_EXPORT_NAVIGATION,
                    )
                    if kind == "click"
                    else input_port.drag(
                        binding,
                        *points,
                        action=InputAction.ACCOUNT_EXPORT_NAVIGATION,
                    )
                )
            except BaseException as exc:
                raise ExportCaptureError("account verification input unavailable") from exc
            if delivered is not True:
                raise ExportCaptureError("account verification input failed")
            sleep(delay)
        exported = clipboard_read()
        if not exported or exported == previous:
            raise ExportCaptureError("export did not replace the clipboard")
        return summarize_world_export(
            exported, expected_tag_sha256=expected_tag_sha256, now=now
        )
    finally:
        exported = ""
        previous = ""
        clipboard_clear()


__all__ = [
    "EXPORT_BUTTON", "MORE_SETTINGS_BUTTON", "SETTINGS_BUTTON", "ExportCaptureError",
    "WorldExportSummary", "capture_world_export", "clear_windows_clipboard",
    "read_windows_clipboard", "summarize_world_export",
]
