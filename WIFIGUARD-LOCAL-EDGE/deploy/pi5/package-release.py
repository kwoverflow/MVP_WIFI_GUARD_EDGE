"""Build a complete application ZIP: real Pi5 UI, all server code and original FP32 model.

OS packages, secrets, databases, sensor data and Android private signing files are
never copied. A release is built locally; no Pi5 is contacted and nothing is deployed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui-bundle", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-id", default="pi5-20261015")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output exists; refusing to replace it")
    profile = Path(__file__).resolve().parent
    root = profile.parents[2]
    if args.model.name != "model-fp32.onnx":
        parser.error("the service requires the original model-fp32.onnx")
    metadata = args.model.with_suffix(".json")
    spec = importlib.util.spec_from_file_location("verify_pi5_release", profile / "verify-release.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    with tempfile.TemporaryDirectory(prefix="wifiguard-pi5-package-") as temporary:
        overlay = Path(temporary) / "code-ui.zip"
        subprocess.run([sys.executable, str(profile / "package-controls.py"), "--profile", "pi5",
                        "--ui-bundle", str(args.ui_bundle.resolve()), "--output", str(overlay)], check=True)
        with ZipFile(overlay) as archive:
            payload = {name: archive.read(name) for name in archive.namelist() if name != "PI5-CONTROLS-MANIFEST.json"}
    payload.update({"models/model-fp32.onnx": args.model.read_bytes(),
                    "models/model-fp32.json": metadata.read_bytes(),
                    "INSTALL-PI5.md": (profile / "RELEASE.md").read_bytes(),
                    "WIFIGUARD-BACKEND/deploy/aws/init-timescale.sql": (root / "WIFIGUARD-BACKEND/deploy/aws/init-timescale.sql").read_bytes(),
                    "SOFTWARE-VALIDATION.json": args.evidence.read_bytes()})
    for path in sorted(args.wheelhouse.iterdir()):
        if not path.is_file() or path.is_symlink() or path.suffix != ".whl":
            parser.error("wheelhouse must contain only verified wheel files")
        payload["WIFIGUARD-LOCAL-EDGE/deploy/pi5/wheelhouse/" + path.name] = path.read_bytes()
    for name, data in payload.items():
        if (b"-----BEGIN " + b"PRIVATE KEY-----") in data or (b"-----BEGIN " + b"RSA PRIVATE KEY-----") in data:
            parser.error("a private key must never be packaged")
        if name.endswith(".json"):
            value = json.loads(data)
            if isinstance(value, dict) and value.get("type") == "service_account":
                parser.error("a server service-account file must never be packaged")
    manifest = {"kind": "pi5-full-application", "release_id": args.release_id,
                "created_at_utc": datetime.now(UTC).isoformat(), "target": "Ubuntu 24.04 ARM64 / Python 3.12 / Node 22 / PostgreSQL 16 + TimescaleDB",
                "includes_model": True, "includes_private_credentials": False,
                "includes_database_or_sensor_data": False, "pi5_deployed": False,
                "includes_arm64_dependency_wheels": True,
                "hardware_validated_this_change": False, "arm64_native_execution_tested_this_change": False,
                "requires_os_packages_and_private_configuration": True,
                "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(payload.items())}}
    verified = verifier.validate(manifest, payload.__getitem__)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "x", compression=ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            archive.writestr(name, data)
        archive.writestr(verifier.MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("release ZIP CRC failure")
    print(json.dumps({**verified, "archive": str(args.output.resolve()), "bytes": args.output.stat().st_size,
                      "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
