"""Offline-only preparation of supplied attack evidence; never runtime admission.

No production composition, transport, capture, authority issuer or network is used.
The command writes only a NEW ignored private pack. Console output is allowlisted;
confidential hashes, filenames and calibration remain inside that pack.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys

ATTACK_TREE = "f8bca55d26b3e79051ecd70b93cb29465f07ad71"
PACKAGE = "com.supercell.clashofclans"
PACK_NAME = "attack-native-readiness-v1"


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii") + b"\n"


def require(value, reason):
    if not value:
        raise ValueError(reason)


def confined(root, relative):
    """Reject links/reparse points on every existing component before reading."""
    root = Path(root).absolute()
    relative = Path(relative)
    require(not relative.is_absolute() and ".." not in relative.parts, "PATH_INVALID")
    candidate = root / relative
    require(candidate.is_relative_to(root), "PATH_ESCAPE")
    for part in (*reversed(candidate.parents), candidate):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            require(not stat.S_ISLNK(info.st_mode)
                    and not getattr(info, "st_file_attributes", 0) & 0x400,
                    "PATH_LINK")
    require(candidate.resolve().is_relative_to(root.resolve()), "PATH_ESCAPE")
    return candidate


def read(root, relative, limit=4 * 1024 * 1024):
    path = confined(root, relative)
    require(path.is_file() and path.stat().st_size <= limit, "FILE_UNAVAILABLE")
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    require((before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns)
            and len(raw) <= limit, "FILE_CHANGED")
    confined(root, relative)
    return raw


def missing_inputs():
    """Exact source schema fields, not invented operational placeholders."""
    return [
        {"fields": ["provider.account_ref", "provider.slot", "provider.slot_id", "provider.slots_digest",
                    "provider.configuration_digest", "provider.instance_digest"],
         "source": "Explicit schema-2 slot/internal-instance mapping and exact configuration snapshot",
         "assertion": "Instance selection and configuration binding; account_ref is an opaque routing key", "status": "BLOCKED_MISSING_INPUT"},
        {"fields": ["supported_home", "native_mapping", "source_screen_witnesses"],
         "source": "Current reviewed instance-bound calibration and fresh Home observation",
         "assertion": "Supported UI and native mapping, not in-game account or package identity", "status": "FUTURE_NATIVE_UNPROVEN"},
        {"fields": ["provider.source_tree", "provider.profile_digest", "RunBindingV3.provider_digest"],
         "source": "Final exact profile and completed canonical provider descriptor",
         "assertion": "Full provider seal; partial draft must not be admitted", "status": "BLOCKED_MISSING_INPUT"},
        {"fields": ["RunBindingV3.approval_digest", "RunBindingV3.runner_manifest_digest",
                    "RunBindingV3.binding_digest", "BuildBinding.process_identity"],
         "source": "Separate reviewed installed artifact, owner envelope and current owned native binding",
         "assertion": "Future authority/native identity only; never issue a run here", "status": "FUTURE_NATIVE_UNPROVEN"},
    ]


def query_specification():
    """Non-executable tokens: exact transport grammar, never host commands."""
    return {
        "status": "STRICT_GUEST_BUILD_PROVIDER_DEFERRED_BY_OWNER", "executed": False,
        "package": PACKAGE, "budget_seconds": 15.0, "max_output_bytes": 256 * 1024,
        "prefix": ["<reviewed-bundled-client>", "-P", "<owned-isolated-server-port>"],
        "tails": [["nodaemon", "server"], ["devices", "-l"],
                  ["connect", "<verified-player-loopback-endpoint>"],
                  ["-s", "<discovered-serial>", "get-serialno"],
                  ["-s", "<discovered-serial>", "shell", "dumpsys", "package", PACKAGE]],
        "package_reads": 2, "package_reads_scope": "per transport.read; acquire plus consume performs four reads",
        "requirements": ["retained process creation identity", "exact configuration/instance digests",
                         "exclusive loopback listeners and connection ownership", "two equal complete package records",
                         "post-read source/serial/connection recheck", "owned daemon cleanup and preserved servers"],
        "activation": "DENIED: _NativeLeaves has no bound capability issuer",
        "cryptographic_build_identity": "APK and signing certificate hashes UNAVAILABLE",
    }


def collect(source, code_root):
    """Verify all permitted inputs before any copy; do not run historical scripts."""
    source = Path(source)
    code_root = Path(code_root)
    provenance_raw = read(source, "INPUT_PROVENANCE.json")
    provenance = json.loads(provenance_raw)
    result_raw = read(source, "c-correction/export/results.json")
    result = json.loads(result_raw)
    require(result["profile_reviewed_tree"] == ATTACK_TREE, "CODE_PROVENANCE")
    module_raw = read(code_root, "src/clash_rush_rebuild/mvp_deployment_profile.py")
    require(sha256(module_raw) == result["module_sha256"], "CODE_PROVENANCE")
    relative = "c-correction/export/offline-pack/private/deployment/"
    profile_raw = read(source, relative + "profile.json", 256 * 1024)
    data = json.loads(profile_raw)
    require(canonical(data) == profile_raw and data["reviewed_tree"] == ATTACK_TREE
            and data["runtime_reviewed"] is False and data["schema"] == 3, "PROFILE_PROVENANCE")
    files = {"original/profile.json": profile_raw, "original/INPUT_PROVENANCE.json": provenance_raw,
             "original/results.json": result_raw}
    for entry in data["assets"].values():
        name = entry["file"]
        require(type(name) is str and re.fullmatch(r"[a-zA-Z0-9_.-]+\.png", name), "ASSET_PATH")
        raw = read(source, relative + "templates/" + name, 2 * 1024 * 1024)
        require(sha256(raw) == entry["sha256"], "ASSET_DIGEST")
        files["offline-pack/private/deployment/templates/" + name] = raw
    frames = provenance["frames"]
    require(len(frames) == 4 and {r["label"] for r in frames}
            == {"stable_home", "battle_menu_fee", "army_review_1", "army_review_2"}, "FRAME_SET")
    for row in frames:
        require(Path(row["file"]).name == row["file"], "FRAME_PATH")
        raw = read(source, row["file"], 32 * 1024 * 1024)
        require(sha256(raw) == row["sha256"], "FRAME_DIGEST")
        files["frames/" + row["file"]] = raw
    return data, files, frames


def prepare(source, project, code_root, candidate_tree):
    require(type(candidate_tree) is str and re.fullmatch(r"[0-9a-f]{40}", candidate_tree), "TREE_INVALID")
    project = confined(Path(project).absolute(), ".")
    require(project.is_dir(), "PROJECT_UNAVAILABLE")
    destination = confined(project, "private/" + PACK_NAME)
    require(not destination.exists(), "DESTINATION_EXISTS")
    ignored = subprocess.run(["git", "-C", str(project), "check-ignore", "--quiet", "--",
                              "private/" + PACK_NAME + "/manifest.json"], check=False)
    require(ignored.returncode == 0, "DESTINATION_NOT_IGNORED")
    data, files, frames = collect(source, code_root)
    # This is an offline tree binding, NOT a review or version claim. No other
    # profile field or asset changes, and runtime_reviewed remains False.
    original_digest = sha256(files["original/profile.json"])
    data["reviewed_tree"] = candidate_tree
    files["offline-pack/private/deployment/profile.json"] = canonical(data)
    for name, raw in files.items():
        path = confined(destination, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        confined(destination, name)
        with path.open("xb") as stream:
            stream.write(raw)
        require(read(destination, name, 32 * 1024 * 1024) == raw, "COPY_MISMATCH")
    manifest = {
        "schema": 1, "candidate_tree": candidate_tree, "source_tree": ATTACK_TREE,
        "source_profile_sha256": original_digest,
        "prepared_profile_sha256": sha256(files["offline-pack/private/deployment/profile.json"]),
        "files": {name: sha256(raw) for name, raw in sorted(files.items())},
        "frames": frames, "missing": missing_inputs(),
        "provider_descriptor": "ABSENT; no fake provider.json created",
        "query_specification": query_specification(),
        "runtime_admission": False, "native_executed": False,
        "expectations": {
            "package": PACKAGE, "renderer_size": data["size"] if "size" in data else None,
            "geometry_status": "FILE_GEOMETRY_ONLY_NOT_NATIVE",
            "profile_digest": sha256(files["offline-pack/private/deployment/profile.json"]),
            "provider_status": "BLOCKED_MISSING_INPUT",
            "profile_status": "FITTED_PRECOMMIT_ONLY_NOT_PRODUCTION_CALIBRATION",
        },
    }
    (destination / "manifest.json").write_bytes(canonical(manifest))
    return destination


def validate(pack, code_root):
    """Real profile loader/observer over file inputs; installed reader is INERT."""
    import cv2
    import numpy as np
    from clash_rush_rebuild import mvp_deployment_profile as module
    from clash_rush_rebuild.mvp_deployment_profile import load_profile, FrameObserver, InstallationEvidence
    from clash_rush_rebuild.mvp_deployment import DeploymentError, Intervention, Screen

    code_root = Path(code_root).resolve(strict=True)
    require(Path(module.__file__).resolve().is_relative_to(code_root / "src"), "WRONG_IMPORT_ROOT")
    pack = Path(pack)
    manifest = json.loads(read(pack, "manifest.json"))
    for name, digest in manifest["files"].items():
        require(sha256(read(pack, name, 32 * 1024 * 1024)) == digest, "PACK_CHANGED")
    original = json.loads(read(pack, "original/profile.json"))
    prepared = json.loads(read(pack, "offline-pack/private/deployment/profile.json"))
    result = json.loads(read(pack, "original/results.json"))
    require(sha256(read(code_root, "src/clash_rush_rebuild/mvp_deployment_profile.py"))
            == result["module_sha256"] and result["profile_reviewed_tree"] == ATTACK_TREE,
            "CODE_PROVENANCE")
    expected = dict(original, reviewed_tree=manifest["candidate_tree"])
    require(prepared == expected and prepared["runtime_reviewed"] is False, "PROFILE_MUTATED")
    profile = load_profile(pack / "offline-pack", candidate_tree=manifest["candidate_tree"], runtime=False)
    require(profile.digest == manifest["prepared_profile_sha256"], "PROFILE_DIGEST")
    try:
        load_profile(pack / "offline-pack", candidate_tree=manifest["candidate_tree"], runtime=True)
    except DeploymentError:
        pass
    else:
        raise ValueError("RUNTIME_NOT_DENIED")
    binding = object()

    def observe(frame, installed=True):
        pixels = frame.copy()
        observer = FrameObserver(profile=profile, capture=lambda: pixels, monotonic=lambda: 1.,
            intervention=lambda: Intervention.CLEAR, home_verified=lambda _: False,
            build_binding=binding, installed_build=(lambda b: InstallationEvidence(
                b, PACKAGE, profile.plan.source_context[3])) if installed else None)
        try:
            return observer()
        finally:
            # Missing installation proof fails BEFORE capture; that input was
            # never owned by observer. All delivered arrays must be wiped.
            if installed:
                require(not pixels.any(), "PIXELS_NOT_WIPED")
            pixels.fill(0)

    positives = 0
    negatives = 0
    for row in manifest["frames"]:
        raw = read(pack, "frames/" + row["file"], 32 * 1024 * 1024)
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        require(frame is not None and frame.shape == (row["height"], row["width"], 3), "FRAME_GEOMETRY")
        try:
            observed = observe(frame)
            expected_screen = (Screen.ARMY if row["label"].startswith("army") else
                               Screen.HOME if row["label"] == "stable_home" else Screen.MATCH)
            require(observed.screen is expected_screen, "OBSERVATION_MISMATCH")
            if expected_screen is Screen.ARMY:
                require(observed.source_inventory == profile.plan.source_inventory
                        and observed.source_categories == profile.plan.source_categories
                        and observed.source_context == profile.plan.source_context, "SOURCE_MISMATCH")
                observed.control("army_attack")
            elif expected_screen is Screen.MATCH:
                require(observed.match_cost == profile.plan.match_entry_cost
                        and observed.match_mode == "REGULAR_MULTIPLAYER", "MATCH_MISMATCH")
                observed.control("find_match")
            else:
                observed.control("attack")
                require(observed.home_verified is False, "FALSE_ACCOUNT_PROOF")
            positives += 1
            try:
                observe(frame, installed=False)
            except DeploymentError:
                negatives += 1
            else:
                raise ValueError("INSTALLATION_NOT_DENIED")
        finally:
            frame.fill(0)
    require(positives == negatives == 4, "INCOMPLETE_FIT")
    return {"profile_schema_and_seals": "PASS", "recorded_precommit_fit": "PASS",
            "source_categories_quantity_and_plan": "PASS",
            "recorded_frames_passed": positives, "missing_installation_denied": negatives,
            "runtime_profile_denied": "PASS", "account_proof": "BLOCKED_MISSING_INPUT",
            "provider_binding": "BLOCKED_MISSING_INPUT", "installed_build": "FUTURE_NATIVE_UNPROVEN",
            "native_geometry": "FUTURE_NATIVE_UNPROVEN", "native_executed": False,
            "installation_reader": "INERT_DOUBLE_NOT_INSTALLED_EVIDENCE",
            "postcommit_artwork": "SYNTHETIC_SCAFFOLD_NOT_CALIBRATION",
            "overall": "BLOCKED_MISSING_INPUT"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--code-root", type=Path, required=True)
    parser.add_argument("--tree", required=True)
    args = parser.parse_args()
    try:
        pack = prepare(args.source, args.project, args.code_root, args.tree)
        result = validate(pack, args.code_root)
        (pack / "offline-result.json").write_bytes(canonical(result))
    except Exception:
        # Never echo paths, calibration, parser payloads or private tracebacks.
        print('{"overall":"OFFLINE_CHECK_FAILED"}')
        return 1
    print(json.dumps(result, sort_keys=True))
    return 2  # expected input blocker, never a readiness PASS exit


if __name__ == "__main__":
    sys.exit(main())
