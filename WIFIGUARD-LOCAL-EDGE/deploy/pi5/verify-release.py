"""Verify a prepared/extracted release using only Python's standard library. No hardware/network."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

MANIFEST = "PI5-RELEASE-MANIFEST.json"
MODEL = "models/model-fp32.onnx"
METADATA = "models/model-fp32.json"
MARKER = "WIFIGUARD-FRONTEND/.output/pi5-build.json"
REQUIRED = {MODEL, METADATA, MARKER, "INSTALL-PI5.md", "SOFTWARE-VALIDATION.json",
    "WIFIGUARD-FRONTEND/.output/public/wifi-guard-logo.png",
    "WIFIGUARD-FRONTEND/dist/server/server.js", "WIFIGUARD-FRONTEND/scripts/serve-pi5.mjs",
    "WIFIGUARD-BACKEND/deploy/aws/init-timescale.sql",
    "WIFIGUARD-BACKEND/packages/db/alembic.ini",
    "WIFIGUARD-BACKEND/packages/db/src/wifiguard_db/migrations/versions/0005_mobile_push.py",
    "WIFIGUARD-BACKEND/services/notification/src/wifiguard_notify/mobile.py",
    "WIFIGUARD-LOCAL-EDGE/deploy/pi5/notification-preflight.py",
    "WIFIGUARD-LOCAL-EDGE/deploy/pi5/requirements-arm64.lock"}


def verify_wheels(files, read):
    locked, hashes = {}, set()
    for name in ("requirements-arm64.lock", "requirements-gpio.lock"):
        text = read("WIFIGUARD-LOCAL-EDGE/deploy/pi5/" + name).decode()
        for package, version in re.findall(r"^([\w.-]+)==([^\s]+)", text, re.M):
            locked[re.sub(r"[-_.]+", "-", package).lower()] = version
        hashes.update(re.findall(r"--hash=sha256:([0-9a-f]{64})", text))
    found = {}
    for name in files:
        if not name.startswith("WIFIGUARD-LOCAL-EDGE/deploy/pi5/wheelhouse/"):
            continue
        if not name.endswith(".whl") or not (name.endswith("-none-any.whl") or ("manylinux" in name and "aarch64" in name)):
            raise ValueError("Wheel must target pure Python or Linux ARM64")
        data = read(name)
        if hashlib.sha256(data).hexdigest() not in hashes:
            raise ValueError("Wheel is not present in the hash-locked dependency set")
        with ZipFile(io.BytesIO(data)) as wheel:
            # setuptools also bundles vendor .dist-info directories. Only the
            # wheel's top-level metadata identifies the distribution we install.
            metadata_files = [p for p in wheel.namelist()
                              if p.count("/") == 1 and p.endswith(".dist-info/METADATA")]
            if len(metadata_files) != 1:
                raise ValueError("Invalid wheel metadata")
            from email.parser import BytesParser
            metadata = BytesParser().parsebytes(wheel.read(metadata_files[0]))
            package = re.sub(r"[-_.]+", "-", metadata["Name"]).lower()
            version = metadata["Version"]
            if locked.get(package) != version or package in found:
                raise ValueError("Wheel does not match the exact runtime version or is duplicated")
            found[package] = version
    if found != locked:
        raise ValueError("Complete ARM64 dependency wheelhouse is required")
    return len(found)


def validate(manifest, read):
    if manifest.get("kind") != "pi5-full-application" or manifest.get("includes_private_credentials") is not False:
        raise ValueError("Not a supported public Pi5 application release")
    files = manifest["files"]
    if not REQUIRED <= files.keys():
        raise ValueError("Required release files are missing")
    for name, expected in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe release path")
        if any(p in {".git", ".venv", ".venv-onnx", "local-output", "postgres-data", "__pycache__"} for p in relative.parts):
            raise ValueError("Runtime/private state must not be distributed")
        if relative.name.startswith(".env") or relative.suffix.lower() in {".key", ".pem", ".jks", ".keystore", ".db", ".sqlite"}:
            raise ValueError("Unexpected private/runtime file")
        data = read(name)
        if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError("Release hash mismatch: " + name)
        if relative.suffix in {".sh", ".service", ".rules", ".conf"} and b"\r" in data:
            raise ValueError("Linux service/script must use LF: " + name)
    marker = json.loads(read(MARKER))
    if marker.get("kind") != "pi5-local-ui" or marker.get("api_base") != "same-origin" or marker.get("mock") is not False or marker.get("pc_simulation") is not False:
        raise ValueError("Pi5 operating UI required; PC simulation bundle is not allowed")
    model = json.loads(read(METADATA))
    if (model.get("contract") != "inhouse-fixed-delay-s3-acf-v1"
        or model.get("precision") != "fp32" or model.get("regression_gate_passed") is not True
        or model.get("architecture_changed") is not False or model.get("retrained") is not False
        or model.get("onnx_sha256") != files[MODEL]["sha256"]):
        raise ValueError("Original validated FP32 model and matching metadata required")
    lock = read("WIFIGUARD-LOCAL-EDGE/deploy/pi5/requirements-arm64.lock").decode()
    for dependency in ("google-auth==", "requests==", "python-dotenv=="):
        if not any(line.startswith(dependency) for line in lock.splitlines()):
            raise ValueError("Notification runtime missing from ARM64 lock")
    wheels = verify_wheels(files, read)
    evidence = json.loads(read("SOFTWARE-VALIDATION.json"))
    if (evidence.get("kind") != "pi5-software-release-validation"
        or evidence.get("software_checks_passed") is not True
        or evidence.get("hardware_tested") is not False
        or evidence.get("arm64_native_execution_tested") is not False):
        raise ValueError("Passing PC software evidence with explicit hardware exclusions is required")
    return {"valid": True, "files_verified": len(files), "ui_pc_simulation": False,
            "ui_api": "same-origin", "model_precision": "fp32", "hardware_tested": False,
            "credentials_included": False, "dependency_wheels_verified": wheels,
            "release_id": manifest["release_id"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="release ZIP or extracted release directory")
    args = parser.parse_args(argv)
    try:
        if args.target.is_dir():
            root = args.target.resolve()
            def read(name):
                original = root / name
                path = original.resolve()
                if not path.is_relative_to(root) or original.is_symlink():
                    raise ValueError("Release path escapes root")
                return path.read_bytes()
            result = validate(json.loads(read(MANIFEST)), read)
        else:
            with ZipFile(args.target) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or archive.testzip() is not None:
                    raise ValueError("Corrupt or duplicate ZIP entries")
                manifest = json.loads(archive.read(MANIFEST))
                if set(names) != set(manifest["files"]) | {MANIFEST}:
                    raise ValueError("Unlisted ZIP files")
                result = validate(manifest, archive.read)
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(json.dumps({"valid": False, "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
