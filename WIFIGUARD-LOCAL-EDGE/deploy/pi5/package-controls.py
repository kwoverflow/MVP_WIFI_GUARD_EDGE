"""Prepare a code/UI overlay for an EXISTING Pi5 checkout; never include secrets/data.

Run on the PC, not the Pi5. Existing models, virtualenv, private pi5.env, DB and
state are reused. This is not a new OS image or an automatic deploy/installer.
"""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("pi5", "pc"), default="pi5")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    ui = args.ui_bundle.resolve()
    marker = json.loads((ui / ".output/pi5-build.json").read_text())
    if marker.get("api_base") != "same-origin" or marker.get("mock") is not False:
        parser.error("same-origin non-mock Pi5 UI required")
    if bool(marker.get("pc_simulation")) != (args.profile == "pc"):
        parser.error("PC and operating Pi5 UI bundles must not be mixed")
    if args.output.exists():
        parser.error("output exists; refusing to replace it")
    packages = ["WIFIGUARD-RASPBERRY", "WIFIGUARD-LOCAL-EDGE"] + [
        "WIFIGUARD-BACKEND/" + p for p in ("packages/contracts", "packages/db", "services/api",
                                         "services/ingest", "services/notification", "services/csi-fall-segmentation")]
    files = {}
    for package in packages:
        base = root / package
        for path in (base / "src").rglob("*.py"):
            if not path.is_symlink():
                files[path.relative_to(root).as_posix()] = path
        files[f"{package}/pyproject.toml"] = base / "pyproject.toml"
    profile = Path(__file__).resolve().parent
    profile_names = {
        "49-wifiguard-edge-control.rules", "backend-requirements.txt", "bootstrap-db.py",
        "create-env.py", "install.sh", "nginx.conf", "package-controls.py", "pi5.env.example",
        "postgresql.conf.example", "preflight.py", "prepare-controls.sh", "README.md",
        "requirements-arm64.lock", "validate-ui.py", "wifiguard-pi5-api.service",
        "wifiguard-pi5-control.service", "wifiguard-pi5-edge.service",
        "wifiguard-pi5-monitor.service", "wifiguard-pi5-ui.service",
        "notification-preflight.py", "verify-release.py", "package-release.py", "RELEASE.md",
        "gpio-requirements.txt", "requirements-gpio.lock",
        "prepare-network.sh", "open-wifi-gui.ps1", "wifiguard-pi5-network.service",
    }
    for name in sorted(profile_names):
        path = profile / name
        if not path.is_file() or path.is_symlink():
            parser.error(f"missing or linked profile source: {name}")
        files[path.relative_to(root).as_posix()] = path
    if args.profile == "pc":
        for path in (profile.parent / "pc").iterdir():
            if path.is_file() and not path.is_symlink() and path.suffix in (".ps1", ".sql", ".md", ".txt", ".py"):
                files[path.relative_to(root).as_posix()] = path
        # The PC handoff also contains reviewable monorepo frontend source;
        # operating Pi5 overlays keep only the generated runtime UI.
        frontend = root / "WIFIGUARD-FRONTEND"
        for source in (frontend / "src", frontend / "scripts"):
            for path in source.rglob("*"):
                if path.is_file() and not path.is_symlink() and path.suffix in (".ts", ".tsx", ".css", ".mjs", ".json"):
                    files[path.relative_to(root).as_posix()] = path
        for name in ("package-lock.json", "vite.config.ts", "tsconfig.json", "eslint.config.js"):
            path = frontend / name
            if path.is_file():
                files[path.relative_to(root).as_posix()] = path
    for name in ("WIFIGUARD-BACKEND/packages/db/alembic.ini", "WIFIGUARD-BACKEND/packages/contracts/openapi.json",
                 "WIFIGUARD-BACKEND/packages/contracts/realtime.schema.json", "WIFIGUARD-LOCAL-EDGE/deploy/onnx-runtime-requirements.txt"):
        files[name] = root / name
    for path in ui.rglob("*"):
        if path.is_file():
            if path.is_symlink() or path.name.startswith(".env") or path.suffix in (".key", ".pem"):
                parser.error("unexpected private file in UI bundle")
            files["WIFIGUARD-FRONTEND/" + path.relative_to(ui).as_posix()] = path
    if args.profile == "pc":
        files["WIFIGUARD-FRONTEND/package.json"] = root / "WIFIGUARD-FRONTEND/package.json"
    manifest = {"kind": args.profile + "-controls-code-ui-overlay", "requires_existing_checkout_and_fp32_model": True,
                "includes_private_credentials": False, "includes_database_or_sensor_data": False,
                "tests": "See validation results; packaging does not run tests",
                "pi5_deployed": False, "hardware_validated_this_change": False, "files": {}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "x", compression=ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            data = path.read_bytes()
            if path.suffix in {".sh", ".service", ".rules", ".conf"}:
                data = data.replace(b"\r\n", b"\n")
            manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            archive.writestr(name, data)
        archive.writestr("PI5-CONTROLS-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("archive verification failed")
    print(json.dumps({"archive": str(args.output.resolve()), "files": len(files),
                      "bytes": args.output.stat().st_size, "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
