from __future__ import annotations

import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path


_REQUIRED_NOTICE_TEXT = (
    "Copyright (c) 2026 Efe Bolukbasi",
    "Copyright (c) 2026 Caleb Welsh",
    "Permission is hereby granted, free of charge, to any person obtaining a copy",
    'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND',
)


def _assert_complete_notice(payload: str) -> None:
    for required in _REQUIRED_NOTICE_TEXT:
        assert required in payload


def test_wheel_and_sdist_package_complete_donor_notices(tmp_path, monkeypatch) -> None:
    source = Path(__file__).parents[1]
    project = tmp_path / "project"
    shutil.copytree(
        source,
        project,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "var"),
    )
    monkeypatch.chdir(project)
    dist = tmp_path / "dist"
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--sdist",
            "--out-dir",
            str(dist),
            "--offline",
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        notice_names = [
            name for name in archive.namelist() if name.endswith("THIRD_PARTY_NOTICES.md")
        ]
        assert len(notice_names) == 1
        _assert_complete_notice(archive.read(notice_names[0]).decode("utf-8"))

    with tarfile.open(sdists[0], "r:gz") as archive:
        notice_members = [
            member
            for member in archive.getmembers()
            if member.name.endswith("THIRD_PARTY_NOTICES.md")
        ]
        assert len(notice_members) == 1
        stream = archive.extractfile(notice_members[0])
        assert stream is not None
        _assert_complete_notice(stream.read().decode("utf-8"))
