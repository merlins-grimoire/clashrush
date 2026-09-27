"""Bounded private diagnostic startup, adapted from first-party ClashRush.

Source: 949497bf0a543a43ec6ef39a8c897e366bc10362, bluestacks_launcher
(block signature/stability), instance_switch (ordered popup loop), observe
(paired crosshair evidence). No donor lifecycle or broad input is imported.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .approval_reconciliation import _seal_private_path
from .input_authorization import InputAction
from .startup_failure import StartupFailure, StartupFault, StartupReason, fault_from, raise_fault
from .startup_continue_recovery import (
    StartupContinueResult as Result,
)
from .startup_continue_recovery import (
    find_continue,
    find_update,
)

LAUNCH_X = 0.4203516249334044
LAUNCH_Y = 0.28599801390268126
# Public generic icon signature from the pinned donor, not account evidence.
LAUNCH_SIGNATURE = "28a8d020a8e090a8d080b0d050a0d04890c840508060a0d02080a04058985040886090d03078a06828688030804878c8"
CAPS = {
    InputAction.STARTUP_LAUNCH_GAME: 1,
    InputAction.STARTUP_CLOSE_PROMO: 3,
    InputAction.STARTUP_OKAY: 1,
    InputAction.STARTUP_CONTINUE: 1,
}


class StartupDebugError(RuntimeError):
    """Sanitized diagnostic failure; never include pixels or paths."""


class StartupDebugFailure(StartupFailure, StartupDebugError):
    """Closed envelope at the controller's production return boundary."""


def _frame(frame):
    if (
        type(frame) is not np.ndarray
        or frame.dtype != np.uint8
        or frame.ndim != 3
        or frame.shape[2] != 3
        or not 64 <= frame.shape[0] <= 2160
        or not 64 <= frame.shape[1] <= 3840
    ):
        raise StartupDebugError("FRAME_INVALID")
    return frame


def launcher_position(frame):
    """Donor 4x4 quantized BGR icon guard; fixed reviewed tolerance, not tunable."""
    h, w = _frame(frame).shape[:2]
    x0, x1 = int((LAUNCH_X - 0.028) * w), int((LAUNCH_X + 0.028) * w)
    y0, y1 = int((LAUNCH_Y - 0.028) * h), int((LAUNCH_Y + 0.028) * h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    signature = []
    for row in range(4):
        for col in range(4):
            patch = frame[
                y0 + (y1 - y0) * row // 4 : y0 + (y1 - y0) * (row + 1) // 4,
                x0 + (x1 - x0) * col // 4 : x0 + (x1 - x0) * (col + 1) // 4,
            ]
            signature.extend(
                min(255, round(float(v) / 8) * 8) for v in patch.mean(axis=(0, 1))
            )
    reviewed = bytes.fromhex(LAUNCH_SIGNATURE)
    distance = sum(abs(a - b) for a, b in zip(signature, reviewed)) / 48
    return (LAUNCH_X, LAUNCH_Y) if distance <= 2 else None


def _text_present(frame, text, font_path):
    """Exact rendered static text anchor, fenced by the caller; no OCR/network."""
    if font_path is None:
        return False
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    for size in range(20, 49, 2):
        font = ImageFont.truetype(str(font_path), size)
        box = font.getbbox(text)
        image = Image.new("L", (box[2] - box[0], box[3] - box[1]))
        ImageDraw.Draw(image).text((-box[0], -box[1]), text, font=font, fill=255)
        template = np.asarray(image)
        if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
            continue
        _, score, _, _ = cv2.minMaxLoc(
            cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        )
        if math.isfinite(score) and score >= 0.85:
            return True
    return False


def popup_position(frame, action, font_path=None):
    """Donor HSV bands with uniqueness veto, actual centroid, and Okay context."""
    h, w = _frame(frame).shape[:2]
    if action is InputAction.STARTUP_CLOSE_PROMO:
        roi = (0.90, 0.03, 1.0, 0.14)
    elif action is InputAction.STARTUP_OKAY:
        roi = (0.35, 0.68, 0.65, 0.88)
    else:
        raise StartupDebugError("ACTION_INVALID")
    if action is InputAction.STARTUP_OKAY and not _text_present(
        frame[int(h * 0.15) : int(h * 0.55), int(w * 0.15) : int(w * 0.85)],
        "Welcome Back",
        font_path,
    ):
        return None
    x0, y0, x1, y1 = int(w * roi[0]), int(h * roi[1]), int(w * roi[2]), int(h * roi[3])
    patch = frame[y0:y1, x0:x1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    if action is InputAction.STARTUP_CLOSE_PROMO:
        mask = cv2.inRange(hsv, (0, 150, 150), (12, 255, 255)) | cv2.inRange(
            hsv, (170, 150, 150), (180, 255, 255)
        )
        minimum = max(20, round(200 * w * h / (1280 * 720)))
    else:
        mask = cv2.inRange(hsv, (35, 170, 170), (90, 255, 255))
        minimum = max(40, round(2000 * w * h / (1791 * 1007)))
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    n, _, stats, centers = cv2.connectedComponentsWithStats(mask, 8)
    components = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= minimum]
    if len(components) > 1:
        raise StartupDebugError("AMBIGUOUS_CONTROL")
    if not components:
        return None
    i = components[0]
    cx, cy = centers[i]
    # A boundary-clipped component is not an intact positively located button.
    sx, sy, sw, sh, _ = stats[i]
    if sx <= 0 or sy <= 0 or sx + sw >= patch.shape[1] or sy + sh >= patch.shape[0]:
        raise StartupDebugError("AMBIGUOUS_CONTROL")
    control = patch[sy : sy + sh, sx : sx + sw]
    if action is InputAction.STARTUP_CLOSE_PROMO:
        white = np.all(control >= 210, axis=2).astype(np.uint8)
        ys, xs = np.where(white)
        if len(xs) < 10:
            return None
        glyph = cv2.resize(
            white[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1],
            (21, 21),
            interpolation=cv2.INTER_NEAREST,
        )
        expected = np.zeros((21, 21), np.uint8)
        cv2.line(expected, (1, 1), (19, 19), 1, 5)
        cv2.line(expected, (1, 19), (19, 1), 1, 5)
        intersection = np.count_nonzero(glyph & expected)
        union = np.count_nonzero(glyph | expected)
        if not union or intersection / union < 0.65:
            return None
    if action is InputAction.STARTUP_OKAY and not _text_present(
        control, "Okay", font_path
    ):
        return None
    return (float(cx + x0) / (w - 1), float(cy + y0) / (h - 1))


class StartupDetector:
    def __init__(self, font_path):
        self.font_path = Path(font_path)

    def __call__(self, frame):
        point = launcher_position(frame)
        if point:
            return InputAction.STARTUP_LAUNCH_GAME, point
        if find_update(frame, self.font_path, frame.shape[0]):
            raise StartupDebugError("UPDATE_REQUIRED")
        for action in (InputAction.STARTUP_CLOSE_PROMO, InputAction.STARTUP_OKAY):
            point = popup_position(frame, action, self.font_path)
            if point:
                return action, point
        match = find_continue(frame, self.font_path, frame.shape[0])
        return (InputAction.STARTUP_CONTINUE, (match.x, match.y)) if match else None


class DebugEvidence:
    """Exclusive protected run directory; at most three retained runs, 14 images.

    Full frames are diagnostic-only. Explicit deletion removes only this run's
    regular files. Reaching the retention cap blocks admission, never overwrites.
    """

    def __init__(self, project, *, seal=_seal_private_path):
        self._seal = seal
        self._pending = {}
        self._count = 0
        try:
            root = Path(project).resolve(strict=True)
            parent = root
            for name in ("var", "private-debug"):
                parent = parent / name
                if parent.is_symlink() or (
                    parent.exists() and parent.resolve() != parent
                ):
                    raise ValueError
                parent.mkdir(exist_ok=True)
                if parent.resolve(strict=True) != parent:
                    raise ValueError
            seal(parent, True)
            # The bounded sink never silently deletes pre-existing evidence.
            if len(tuple(parent.iterdir())) >= 3:
                raise ValueError
            self.directory = parent / secrets.token_hex(16)
            self.directory.mkdir()
            seal(self.directory, True)
        except BaseException:  # noqa: BLE001 - privacy boundary
            raise StartupDebugError("EVIDENCE_UNAVAILABLE") from None

    def _write(self, name, raw):
        path = self.directory / name
        descriptor = None
        try:
            if (
                self.directory.resolve(strict=True) != self.directory
                or self.directory.is_symlink()
                or path.name != name
            ):
                raise ValueError
            self._seal(self.directory, True)
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0),
                0o600,
            )
            self._seal(path, False)
            if os.write(descriptor, raw) != len(raw):
                raise OSError
            os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.read(descriptor, len(raw)) != raw:
                raise OSError
        except BaseException:  # noqa: BLE001 - privacy boundary
            raise StartupDebugError("EVIDENCE_WRITE_FAILED") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _image(self, name, frame, point=None):
        if self._count >= 14:
            raise StartupDebugError("EVIDENCE_LIMIT")
        image = None
        try:
            image = _frame(frame).copy()
            if point is not None:
                x, y = (
                    round(point[0] * (image.shape[1] - 1)),
                    round(point[1] * (image.shape[0] - 1)),
                )
                # Adapted observe._mark: red ring and intersecting red lines.
                cv2.circle(image, (x, y), 26, (0, 0, 255), 5)
                cv2.line(image, (x - 20, y), (x + 20, y), (0, 0, 255), 5)
                cv2.line(image, (x, y - 20), (x, y + 20), (0, 0, 255), 5)
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                raise ValueError
            self._write(name, encoded.tobytes())
            self._count += 1
        finally:
            if image is not None:
                image.fill(0)

    def snapshot(self, kind, frame):
        if kind not in {"initial", "final"}:
            raise StartupDebugError("EVIDENCE_INVALID")
        self._image(kind + ".png", frame)

    def before(self, action, frame, point):
        if type(action) is not InputAction or action not in CAPS or self._pending:
            raise StartupDebugError("EVIDENCE_INVALID")
        nonce = secrets.token_hex(16)
        self._image(nonce + "-before.png", frame, point)
        self._write(
            nonce + "-intent.json",
            json.dumps({"action_nonce": nonce, "action": action.value}).encode("ascii"),
        )
        self._pending[nonce] = action
        return nonce

    def after(self, nonce, frame, delivered):
        if (
            type(nonce) is not str
            or nonce not in self._pending
            or type(delivered) is not bool
        ):
            raise StartupDebugError("EVIDENCE_INVALID")
        self._write(
            nonce + "-outcome.json",
            json.dumps({"action_nonce": nonce, "delivered": delivered}).encode("ascii"),
        )
        self._image(nonce + "-after.png", frame)
        del self._pending[nonce]

    def delete(self):
        if (
            self.directory.is_symlink()
            or self.directory.resolve(strict=True) != self.directory
        ):
            raise StartupDebugError("EVIDENCE_INVALID")
        files = tuple(self.directory.iterdir())
        for path in files:
            if not path.is_file() or path.is_symlink() or path.resolve() != path:
                raise StartupDebugError("EVIDENCE_INVALID")
        for path in files:
            path.unlink()
        self.directory.rmdir()

    def verify(self):
        """Read back protected directory and every paired file before input."""
        if (
            self.directory.is_symlink()
            or self.directory.resolve(strict=True) != self.directory
        ):
            raise StartupDebugError("EVIDENCE_INVALID")
        self._seal(self.directory, True)
        for path in self.directory.iterdir():
            if not path.is_file() or path.is_symlink() or path.resolve() != path:
                raise StartupDebugError("EVIDENCE_INVALID")
            self._seal(path, False)


class StartupDebugController:
    """Stable launcher -> one icon click -> settle -> red/Okay/Continue -> village."""

    def __init__(self, *, capture, classify, detect, deliver, evidence, gate):
        self.capture, self.classify, self.detect, self.deliver = (
            capture,
            classify,
            detect,
            deliver,
        )
        self.evidence, self.gate = evidence, gate
        self.used = False

    def run(self, *, timeout=60.0, wait=time.sleep, clock=time.monotonic):
        if self.used or type(timeout) not in (int, float) or not 0 < timeout <= 120:
            raise_fault(StartupFault(StartupReason.AUTHORIZATION), StartupDebugFailure)
        self._stage = StartupReason.AUTHORIZATION
        self.input_completed = False
        failure = None
        result = None
        final = None
        try:
            result = self._run(timeout=timeout, wait=wait, clock=clock)
        except BaseException as exc:
            failure = fault_from(exc, self._stage, input_completed=self.input_completed)
        try:
            final = _frame(self.capture())
            self.evidence.snapshot("final", final)
        except BaseException:
            failure = (failure.superseded(StartupReason.FINAL_EVIDENCE) if failure
                       else StartupFault(StartupReason.FINAL_EVIDENCE, input_completed=self.input_completed))
        finally:
            if type(final) is np.ndarray:
                final.fill(0)
        if failure is not None:
            raise_fault(failure, StartupDebugFailure)
        return result

    def _run(self, *, timeout, wait, clock):
        if self.used or type(timeout) not in (int, float) or not 0 < timeout <= 120:
            raise StartupDebugError("RUN_INVALID")
        self.used = True
        counts = {action: 0 for action in CAPS}
        deadline = clock() + timeout
        previous = None
        frame = fresh = None
        stable = 0
        initial = False
        try:
            while clock() < deadline:
                self._stage = StartupReason.AUTHORIZATION
                if self.gate() is not True:
                    raise StartupDebugError("AUTHORIZATION_LOST")
                self._stage = StartupReason.CAPTURE if initial else StartupReason.INITIAL_CAPTURE
                frame = _frame(self.capture())
                if not initial:
                    if (
                        previous is not None
                        and previous.shape == frame.shape
                        and float(np.abs(previous.astype(np.int16) - frame).mean())
                        <= 0.35
                    ):
                        stable += 1
                    else:
                        stable = 0
                    if previous is not None:
                        previous.fill(0)
                    previous = frame.copy()
                    if stable < 2:
                        frame.fill(0)
                        wait(0.05)
                        continue
                    self._stage = StartupReason.INITIAL_EVIDENCE
                    self.evidence.snapshot("initial", frame)
                    initial = True
                # Blockers precede village acceptance: a dimmed village can retain HUD templates.
                self._stage = StartupReason.ICON_VERIFY
                target = self.detect(frame)
                if target is None:
                    self._stage = StartupReason.RECOGNITION
                    result = self.classify(frame)
                    if type(result) is not Result:
                        raise StartupDebugError("RESULT_INVALID")
                    if result is not Result.UNKNOWN:
                        return result
                else:
                    action, point = target
                    if type(action) is not InputAction or action not in CAPS:
                        raise StartupDebugError("ACTION_INVALID")
                    if counts[action] >= CAPS[action]:
                        return Result.UNKNOWN
                    if action is InputAction.STARTUP_LAUNCH_GAME:
                        # Preserve donor launcher stability even if it appears
                        # after the initial non-launcher observation.
                        for _ in range(2):
                            wait(0.05)
                            self._stage = StartupReason.CAPTURE
                            fresh = _frame(self.capture())
                            self._stage = StartupReason.ICON_VERIFY
                            if (
                                self.detect(fresh) != target
                                or fresh.shape != frame.shape
                                or float(np.abs(fresh.astype(np.int16) - frame).mean())
                                > 0.35
                            ):
                                return Result.UNKNOWN
                            frame.fill(0)
                            frame = fresh
                            fresh = None
                    self._stage = StartupReason.CAPTURE
                    fresh = _frame(self.capture())
                    self._stage = StartupReason.ICON_VERIFY
                    if self.detect(fresh) != target:
                        return Result.UNKNOWN
                    nonce = None

                    def prepare(authorization_frame, action=action, point=point):
                        nonlocal nonce
                        self._stage = StartupReason.EVIDENCE_BEFORE
                        if nonce is not None:
                            raise StartupDebugError("EVIDENCE_INVALID")
                        nonce = self.evidence.before(action, authorization_frame, point)
                        self._stage = StartupReason.INPUT

                    counts[action] += 1
                    self._stage = StartupReason.AUTHORIZATION
                    if clock() >= deadline or self.gate() is not True:
                        raise StartupDebugError("AUTHORIZATION_LOST")
                    delivered = False
                    input_fault = None
                    try:
                        self._stage = StartupReason.INPUT
                        delivered = (
                            self.deliver(action, point, prepare, deadline) is True
                        )
                        self.input_completed = self.input_completed or delivered
                    except BaseException as exc:
                        input_fault = fault_from(exc, self._stage, input_completed=self.input_completed)
                    fresh.fill(0)
                    if nonce is not None:
                        fresh = None
                        try:
                            self._stage = StartupReason.POST_CAPTURE
                            wait(0.6)
                            fresh = _frame(self.capture())
                        except BaseException as exc:
                            post_fault = fault_from(exc, self._stage, input_completed=self.input_completed)
                            input_fault = input_fault.superseded(post_fault.reason) if input_fault else post_fault
                        try:
                            self._stage = StartupReason.POST_EVIDENCE
                            # Persist delivery even when the post camera fails.
                            self.evidence.after(nonce, fresh, delivered)
                        except BaseException as exc:
                            post_fault = fault_from(exc, self._stage, input_completed=self.input_completed)
                            input_fault = input_fault.superseded(post_fault.reason) if input_fault else post_fault
                    if input_fault is not None:
                        raise_fault(input_fault, StartupDebugFailure)
                    if not delivered:
                        self._stage = StartupReason.INPUT
                        raise StartupDebugError("INPUT_FAILED")
                    wait(
                        min(
                            25.0 if action is InputAction.STARTUP_LAUNCH_GAME else 1.5,
                            max(0.0, deadline - clock()),
                        )
                    )
                frame.fill(0)
                if fresh is not None:
                    fresh.fill(0)
                wait(min(0.5, max(0.0, deadline - clock())))
            return Result.UNKNOWN
        finally:
            for item in (frame, fresh, previous):
                if type(item) is np.ndarray:
                    item.fill(0)
