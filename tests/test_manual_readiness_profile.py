from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import cv2
import numpy as np
import pytest

from clash_rush_rebuild.cli import main
from clash_rush_rebuild.manual_readiness_profile import (
    ManualReadinessProfileError,
    build_manual_readiness_profile,
)
from clash_rush_rebuild.mvp_account_ready import load_private_visual_profile


NAMES = {
    "home",
    "settings_button",
    "settings",
    "more_button",
    "more",
    "export",
    "more_close",
    "settings_close",
}


def _review_packet(project: Path) -> Path:
    source = project / "private" / "manual-readiness"
    source.mkdir(parents=True)
    entries: dict[str, object] = {}
    for index, name in enumerate(sorted(NAMES), start=1):
        pixels = np.random.default_rng(index).integers(
            0, 256, size=(20 + index, 30 + index, 3), dtype=np.uint8
        )
        assert cv2.imwrite(str(source / f"{name}.png"), pixels)
        entries[name] = {
            "file": f"{name}.png",
            "threshold_ppm": 900_000,
            "roi_ppm": [0, 0, 1_000_000, 1_000_000],
        }
    manifest = source / "review.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "profile_id": "owner-reviewed-v1",
                "templates": entries,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return manifest


def test_builds_runtime_profile_from_exact_reviewed_narrow_crops(tmp_path: Path) -> None:
    manifest = _review_packet(tmp_path)
    sealed: list[tuple[Path, bool]] = []

    profile_id = build_manual_readiness_profile(
        tmp_path,
        manifest,
        permission_sealer=lambda path, directory: sealed.append((path, directory)),
        nonce_factory=lambda: "a" * 32,
    )

    assert profile_id == "owner-reviewed-v1"
    output = tmp_path / "private" / "readiness"
    profile = load_private_visual_profile(tmp_path, output / "profile.json")
    assert profile.profile_id == profile_id
    assert {spec.name for spec in profile.specs} == NAMES
    payload = json.loads((output / "profile.json").read_text(encoding="utf-8"))
    for name, entry in payload["templates"].items():
        encoded = (output / entry["file"]).read_bytes()
        pixels = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
        assert hashlib.sha256(encoded).hexdigest() == entry["file_sha256"]
        assert hashlib.sha256(pixels.tobytes()).hexdigest() == entry["pixel_sha256"]
        assert max(pixels.shape[:2]) <= 320
    assert any(path.name == ".profile-" + "a" * 32 + ".json" and not directory for path, directory in sealed)


def test_missing_reviewed_crop_leaves_no_runtime_profile_or_assets(tmp_path: Path) -> None:
    manifest = _review_packet(tmp_path)
    (manifest.parent / "more_button.png").unlink()

    with pytest.raises(ManualReadinessProfileError, match="review packet"):
        build_manual_readiness_profile(
            tmp_path,
            manifest,
            permission_sealer=lambda _path, _directory: None,
        )

    output = tmp_path / "private" / "readiness"
    assert not (output / "profile.json").exists()
    assert list(output.glob("*.png")) == [] if output.exists() else True


def test_oversized_or_full_frame_input_is_rejected(tmp_path: Path) -> None:
    manifest = _review_packet(tmp_path)
    full_frame = np.zeros((720, 1280, 3), np.uint8)
    assert cv2.imwrite(str(manifest.parent / "home.png"), full_frame)

    with pytest.raises(ManualReadinessProfileError, match="narrow crop"):
        build_manual_readiness_profile(
            tmp_path,
            manifest,
            permission_sealer=lambda _path, _directory: None,
        )

    assert not (tmp_path / "private" / "readiness" / "profile.json").exists()


def test_corrupt_png_is_a_closed_review_packet_failure(tmp_path: Path) -> None:
    manifest = _review_packet(tmp_path)
    (manifest.parent / "home.png").write_bytes(b"not a png")

    with pytest.raises(ManualReadinessProfileError, match="narrow crop"):
        build_manual_readiness_profile(
            tmp_path,
            manifest,
            permission_sealer=lambda _path, _directory: None,
        )


def test_runtime_target_cannot_be_a_private_path_escape(tmp_path: Path) -> None:
    manifest = _review_packet(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = tmp_path / "private" / "readiness"
    try:
        target.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlinks unavailable")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.skip("directory junctions unavailable")

    with pytest.raises(ManualReadinessProfileError, match="runtime path"):
        build_manual_readiness_profile(
            tmp_path,
            manifest,
            permission_sealer=lambda _path, _directory: None,
        )

    assert not (outside / "profile.json").exists()


def test_private_root_cannot_be_a_junction_outside_project(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    private = project / "private"
    try:
        private.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlinks unavailable")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(private), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.skip("directory junctions unavailable")
    manifest = _review_packet(project)

    with pytest.raises(ManualReadinessProfileError, match="private root"):
        build_manual_readiness_profile(
            project,
            manifest,
            permission_sealer=lambda _path, _directory: None,
        )

    assert not (outside / "readiness" / "profile.json").exists()


def test_existing_runtime_profile_is_never_replaced(tmp_path: Path) -> None:
    manifest = _review_packet(tmp_path)
    output = tmp_path / "private" / "readiness"
    output.mkdir(parents=True)
    existing = output / "profile.json"
    existing.write_bytes(b"existing")

    with pytest.raises(ManualReadinessProfileError, match="already exists"):
        build_manual_readiness_profile(
            tmp_path,
            manifest,
            permission_sealer=lambda _path, _directory: None,
        )

    assert existing.read_bytes() == b"existing"


def test_racing_runtime_profile_creation_is_never_replaced(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _review_packet(tmp_path)
    original_link = os.link

    def racing_link(source: str | Path, destination: str | Path) -> None:
        Path(destination).write_bytes(b"racer")
        original_link(source, destination)

    monkeypatch.setattr("clash_rush_rebuild.manual_readiness_profile.os.link", racing_link)

    with pytest.raises(ManualReadinessProfileError, match="publication failed"):
        build_manual_readiness_profile(
            tmp_path,
            manifest,
            permission_sealer=lambda _path, _directory: None,
            nonce_factory=lambda: "c" * 32,
        )

    assert (tmp_path / "private" / "readiness" / "profile.json").read_bytes() == b"racer"


def test_non_png_reviewed_input_is_rejected(tmp_path: Path) -> None:
    manifest = _review_packet(tmp_path)
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    pixels = cv2.imread(str(manifest.parent / "home.png"))
    assert cv2.imwrite(str(manifest.parent / "home.jpg"), pixels)
    raw["templates"]["home"]["file"] = "home.jpg"
    manifest.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ManualReadinessProfileError, match="review packet"):
        build_manual_readiness_profile(
            tmp_path,
            manifest,
            permission_sealer=lambda _path, _directory: None,
        )


def test_failure_before_manifest_publication_removes_new_assets(tmp_path: Path) -> None:
    manifest = _review_packet(tmp_path)
    calls = 0

    def fail_seal(_path: Path, _directory: bool) -> None:
        nonlocal calls
        calls += 1
        if calls == 5:
            raise OSError("private marker")

    with pytest.raises(ManualReadinessProfileError, match="publication failed"):
        build_manual_readiness_profile(
            tmp_path,
            manifest,
            permission_sealer=fail_seal,
            nonce_factory=lambda: "b" * 32,
        )

    output = tmp_path / "private" / "readiness"
    assert not (output / "profile.json").exists()
    assert list(output.glob("*.png")) == []


def test_cli_build_command_emits_only_closed_success(monkeypatch, capsys) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "clash_rush_rebuild.cli.build_manual_readiness_profile",
        lambda root, manifest: calls.append((root, manifest)) or "private-id",
    )

    status = main(
        [
            "build-readiness-profile",
            "--project-root",
            "ROOT",
            "--review-manifest",
            "PRIVATE",
        ]
    )

    assert status == 0
    assert calls == [("ROOT", "PRIVATE")]
    assert capsys.readouterr() == ("private readiness profile built\n", "")


def test_cli_build_failure_does_not_emit_private_details(monkeypatch, capsys) -> None:
    def fail(_root: str, _manifest: str) -> str:
        raise ManualReadinessProfileError("PRIVATE_ACCOUNT_MARKER")

    monkeypatch.setattr(
        "clash_rush_rebuild.cli.build_manual_readiness_profile",
        fail,
    )

    status = main(
        [
            "build-readiness-profile",
            "--project-root",
            "ROOT",
            "--review-manifest",
            "PRIVATE",
        ]
    )

    assert status == 1
    assert capsys.readouterr() == ("", "private readiness profile build failed\n")


def test_cli_argument_failure_does_not_echo_private_values(capsys) -> None:
    marker = "PRIVATE_EXTRA_MARKER"

    with pytest.raises(SystemExit) as failure:
        main(["build-readiness-profile", "--unexpected", marker])

    assert failure.value.code == 2
    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err
    assert captured.err == "private readiness profile arguments invalid\n"
