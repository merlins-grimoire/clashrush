"""Inert, one-observation source diagnostics; never gameplay authorization.

No launcher or production caller is installed by this module. A future reviewed
composition must supply bounded owned capture and fresh binding revalidation.
Sampled bars/darkness describe evidence, not a semantic loading-screen detector.
"""
from __future__ import annotations

from collections.abc import Callable

from .lifecycle import PlayerBinding, ProcessIdentity
from .mvp_local_runtime import (
    BgraGameplayRecognizer,
    RuntimeSafetyError,
    _clear_capture_tracebacks,
    _home_payload,
    _serialize_home_payload,
)


_BINDING_FIELDS = (
    "identity", "render_identity", "root_hwnd", "render_hwnd",
    "width", "height", "capture_nonce",
)


def _binding_values(binding: PlayerBinding) -> tuple:
    """The owned revalidation port, not these shape checks, proves identity."""
    try:
        if type(binding) is not PlayerBinding:
            raise RuntimeSafetyError("SOURCE_BINDING_INVALID")
        PlayerBinding.__post_init__(binding)
        if binding.width * binding.height * 4 > 64 * 1024 * 1024:
            raise RuntimeSafetyError("SOURCE_BINDING_INVALID")
        # Copy nested identity scalars, not aliases to bypass-mutable dataclasses.
        ProcessIdentity.__post_init__(binding.identity)
        ProcessIdentity.__post_init__(binding.render_identity)
        return (
            (binding.identity.pid, binding.identity.creation_time_100ns),
            (binding.render_identity.pid, binding.render_identity.creation_time_100ns),
            *(object.__getattribute__(binding, name) for name in _BINDING_FIELDS[2:]),
        )
    finally:
        binding = None


def _sample_content(frame: tuple[int, int, bytes]) -> dict[str, int | str]:
    """Fixed 32 by 18 cell centers; no pixel colors or per-cell map escape."""
    pixels = None
    try:
        width, height, pixels = frame
        black = bright = chromatic = 0
        top = bottom = left = right = center = 0
        for row in range(18):
            y = (2 * row + 1) * height // 36
            for column in range(32):
                x = (2 * column + 1) * width // 64
                offset = (y * width + x) * 4
                _, saturation, value = BgraGameplayRecognizer._hsv(
                    pixels[offset], pixels[offset + 1], pixels[offset + 2],
                )
                black += value <= 8
                bright += value >= 120
                chromatic += saturation >= 100
                top += row == 0 and value < 120
                bottom += row == 17 and value < 120
                left += column == 0 and value < 120
                right += column == 31 and value < 120
                center += 4 <= row < 13 and 8 <= column < 24 and value >= 120
        # Opposed dark edges plus a majority-bright center are only suspicion.
        # A naturally framed scene can share this signature; no ROI is moved.
        bars = ((top == bottom == 32) or (left == right == 18)) and center > 72
        content = (
            "BLACK_SAMPLED" if black == 576 else
            "LOW_CHROMA_SAMPLED" if chromatic == 0 else
            "DARK_CHROMATIC_SAMPLED" if bright == 0 else "MIXED_SAMPLED"
        )
        return {
            "geometry": "LETTERBOX_SUSPECT" if bars else "DIMENSIONS_MATCH",
            "content": content,
            "sample_pixels": 576, "sample_black": black,
            "sample_bright": bright, "sample_chromatic": chromatic,
            "top_dark": top, "bottom_dark": bottom,
            "left_dark": left, "right_dark": right,
            "center_pixels": 144, "center_bright": center,
        }
    finally:
        frame = pixels = None


def observe_source(
    binding: PlayerBinding,
    capture_owned: Callable[[PlayerBinding], tuple[int, int, bytes]],
    revalidate: Callable[[PlayerBinding], bool],
) -> str:
    """Return one canonical scalar report, or a closed sanitized exception.

    Both ports are trusted capabilities, not caller-provided evidence. Capture
    must use the existing lifecycle-owned transient capture seam; revalidation
    must freshly prove that exact binding and return the exact bool True. Ports
    must not retain frames. They own their operation deadlines. This function
    has no polling/retry, timer, persistence, input, or native construction.

    Immutable bytes cannot be securely zeroed: cleanup releases owned references
    and clears unwound callback tracebacks, not copies retained by the caller.
    HOME_CUE_PRESENT means only the unchanged local HOME color predicate passed;
    it is never a Recognition, account/army proof, or permission to act.
    """
    frame = pixels = snapshot = diagnostic = payload = result = None
    reason = "SOURCE_BINDING_INVALID"
    failed = False
    try:
        snapshot = _binding_values(binding)
        width, height = snapshot[4:6]
        if not callable(capture_owned) or not callable(revalidate):
            raise RuntimeSafetyError(reason)
        reason = "SOURCE_BINDING_UNVERIFIED"
        if revalidate(binding) is not True or _binding_values(binding) != snapshot:
            raise RuntimeSafetyError(reason)
        reason = "SOURCE_CAPTURE_FAILED"
        frame = capture_owned(binding)
        reason = "SOURCE_BINDING_UNVERIFIED"
        if _binding_values(binding) != snapshot:
            raise RuntimeSafetyError(reason)
        reason = "SOURCE_SHAPE_INVALID"
        if type(frame) is not tuple or len(frame) != 3:
            raise RuntimeSafetyError(reason)
        reason = "SOURCE_GEOMETRY_MISMATCH"
        if (type(frame[0]) is not int or type(frame[1]) is not int
                or (frame[0], frame[1]) != (width, height)):
            raise RuntimeSafetyError(reason)
        reason = "SOURCE_BYTES_INVALID"
        if type(frame[2]) is not bytes or len(frame[2]) != width * height * 4:
            raise RuntimeSafetyError(reason)
        reason = "SOURCE_REDUCTION_FAILED"
        diagnostic = BgraGameplayRecognizer._diagnose_home(frame)
        payload = _home_payload(diagnostic)
        payload.update(_sample_content(frame))
        payload["schema"] = 2
        payload["readiness"] = (
            "HOME_CUE_PRESENT" if payload["reason"] == "HOME_POSITIVE"
            and payload["geometry"] == "DIMENSIONS_MATCH" else "UNVERIFIED"
        )
        result = _serialize_home_payload(payload)
        # No pixels survive into the post-observation callback or returned report.
        frame = None
        reason = "SOURCE_BINDING_UNVERIFIED"
        if revalidate(binding) is not True or _binding_values(binding) != snapshot:
            raise RuntimeSafetyError(reason)
    except BaseException as error:
        _clear_capture_tracebacks(error)
        failed = True
    finally:
        frame = pixels = snapshot = diagnostic = payload = None
        binding = capture_owned = revalidate = None
    if failed:
        result = None
        # Outside the handler: no private cause/context or callback text escapes.
        raise RuntimeSafetyError(reason)
    return result
