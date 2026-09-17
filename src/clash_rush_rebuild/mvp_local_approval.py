"""Single-use live approval bound to one exact local MVP state."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path

LIVE_ACTION = "MVP_ACCOUNT_VERIFY_ATTACK"
_MAX_LIFETIME_SECONDS = 300
_HEX_32 = re.compile(r"[0-9a-f]{32}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_FIELDS = {
    "action", "candidate_tree", "control_sha256", "expires_at", "issued_at",
    "lifecycle_sha256", "nonce", "schema",
}


class ApprovalError(RuntimeError):
    """The live action approval is absent, stale, mismatched, or consumed."""


def canonical_approval(
    *,
    action: str,
    candidate_tree: str,
    issued_at: int,
    expires_at: int,
    lifecycle_sha256: str,
    control_sha256: str,
    nonce: str,
) -> bytes:
    values = (candidate_tree, lifecycle_sha256, control_sha256, nonce)
    if (
        type(action) is not str
        or action != LIVE_ACTION
        or type(candidate_tree) is not str
        or _HEX_40.fullmatch(candidate_tree) is None
        or type(issued_at) is not int
        or type(expires_at) is not int
        or not 0 <= issued_at < expires_at
        or expires_at - issued_at > _MAX_LIFETIME_SECONDS
        or any(type(value) is not str for value in values)
        or _HEX_64.fullmatch(lifecycle_sha256) is None
        or _HEX_64.fullmatch(control_sha256) is None
        or _HEX_32.fullmatch(nonce) is None
    ):
        raise ApprovalError("live approval is malformed")
    return (
        json.dumps(
            {
                "action": action,
                "candidate_tree": candidate_tree,
                "control_sha256": control_sha256,
                "expires_at": expires_at,
                "issued_at": issued_at,
                "lifecycle_sha256": lifecycle_sha256,
                "nonce": nonce,
                "schema": 1,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )


class LiveApprovalStore:
    """Validate and atomically move one approval to the consumed namespace."""

    def __init__(self, project_root: Path) -> None:
        root = Path(project_root).resolve(strict=True)
        self.path = root / "var" / "mvp-live-approval.json"
        self.consumed_dir = root / "var" / "consumed-live-approvals"

    def validate_and_consume(
        self,
        *,
        candidate_tree: str,
        lifecycle_bytes: bytes,
        control_bytes: bytes,
        now: int,
    ) -> None:
        if (
            type(candidate_tree) is not str
            or _HEX_40.fullmatch(candidate_tree) is None
            or type(lifecycle_bytes) is not bytes
            or type(control_bytes) is not bytes
            or type(now) is not int
            or now < 0
        ):
            raise ApprovalError("approval validation inputs are malformed")
        try:
            payload = self.path.read_bytes()
        except OSError as exc:
            raise ApprovalError("live approval is unavailable") from exc
        if len(payload) > 2048:
            raise ApprovalError("live approval is malformed")
        try:
            raw = json.loads(payload.decode("ascii"))
            if type(raw) is not dict or set(raw) != _FIELDS or type(raw["schema"]) is not int or raw["schema"] != 1:
                raise ApprovalError("live approval is malformed")
            canonical = canonical_approval(
                action=raw["action"], candidate_tree=raw["candidate_tree"],
                issued_at=raw["issued_at"], expires_at=raw["expires_at"],
                lifecycle_sha256=raw["lifecycle_sha256"],
                control_sha256=raw["control_sha256"], nonce=raw["nonce"],
            )
        except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, ApprovalError) as exc:
            raise ApprovalError("live approval is malformed") from exc
        if canonical != payload:
            raise ApprovalError("live approval is malformed")
        if not raw["issued_at"] <= now < raw["expires_at"]:
            raise ApprovalError("live approval is expired or not yet valid")
        if not hmac.compare_digest(raw["candidate_tree"], candidate_tree):
            raise ApprovalError("live approval candidate tree mismatch")
        lifecycle_digest = hashlib.sha256(lifecycle_bytes).hexdigest()
        control_digest = hashlib.sha256(control_bytes).hexdigest()
        if not hmac.compare_digest(raw["lifecycle_sha256"], lifecycle_digest):
            raise ApprovalError("live approval lifecycle state mismatch")
        if not hmac.compare_digest(raw["control_sha256"], control_digest):
            raise ApprovalError("live approval control state mismatch")
        self.consumed_dir.mkdir(parents=False, exist_ok=True)
        destination = self.consumed_dir / f"{raw['nonce']}.json"
        if destination.exists():
            raise ApprovalError("live approval was already consumed")
        try:
            os.replace(self.path, destination)
            if destination.read_bytes() != payload:
                raise ApprovalError("consumed approval read-back mismatch")
        except OSError as exc:
            raise ApprovalError("live approval could not be consumed") from exc


__all__ = ["LIVE_ACTION", "ApprovalError", "LiveApprovalStore", "canonical_approval"]
