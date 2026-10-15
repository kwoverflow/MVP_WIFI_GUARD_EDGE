"""Offline notification configuration checks. Never sends, refreshes OAuth or prints secrets."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path


def check(config, *, require_email=False, require_push=False, check_permissions=True):
    errors, warnings = [], []
    email = bool(config.get("SMTP_HOST", ""))
    push = config.get("MOBILE_PUSH_ENABLED", "0") == "1"
    if config.get("MOBILE_PUSH_ENABLED", "0") not in ("0", "1"):
        errors.append("MOBILE_PUSH_ENABLED must be 0 or 1")
    if config.get("PC_SIMULATION_MODE", "0") != "0":
        errors.append("PC_SIMULATION_MODE must be 0 on the operating Pi5")
    if config.get("MOBILE_PC_TEST_PUSH_ENABLED", "0") != "0":
        errors.append("PC-only push override must be disabled")
    if not email:
        (errors if require_email else warnings).append("Email is not configured")
    else:
        for name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"):
            value = config.get(name, "") or ""
            if not value or "REPLACE" in value or value.startswith("YOUR_") or "\r" in value or "\n" in value:
                errors.append(f"Configure {name}; its value is not printed")
        if config.get("SMTP_STARTTLS", "1") != "1":
            errors.append("Operating email requires SMTP_STARTTLS=1")
        try:
            port = int(config.get("SMTP_PORT", "587"))
            if not 1 <= port <= 65535 or port == 465:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Use a valid STARTTLS SMTP port (normally 587, not implicit-TLS 465)")
    if not push:
        (errors if require_push else warnings).append("Android FCM push is disabled")
    else:
        project = config.get("FCM_PROJECT_ID", "") or ""
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", project):
            errors.append("Configure a valid FCM_PROJECT_ID")
        key = Path(config.get("FCM_SERVICE_ACCOUNT_FILE", "") or ".")
        if not key.is_absolute() or not key.is_file() or key.stat().st_size > 16384:
            errors.append("FCM_SERVICE_ACCOUNT_FILE must be a small absolute private JSON file")
        else:
            if any(key.resolve().is_relative_to(Path(p)) for p in ("/home", "/root", "/run/user")):
                errors.append("API ProtectHome cannot read a key under home; use /etc/wifiguard")
            if check_permissions and os.name == "posix":
                import pwd
                service_user = pwd.getpwnam("wifiguard")
                metadata = key.stat()
                mode = stat.S_IMODE(metadata.st_mode)
                readable = (metadata.st_uid == service_user.pw_uid and mode & stat.S_IRUSR) or (
                    metadata.st_gid == service_user.pw_gid and mode & stat.S_IRGRP)
                if mode & 0o007 or mode & 0o022 or not readable:
                    errors.append("Key must be private and readable by the wifiguard API user (0600 owner or 0640 group)")
                # The service needs traversal as well as read permission.
                for parent in key.resolve().parents:
                    meta = parent.stat()
                    bits = stat.S_IMODE(meta.st_mode)
                    traversable = (meta.st_uid == service_user.pw_uid and bits & stat.S_IXUSR) or (
                        meta.st_gid == service_user.pw_gid and bits & stat.S_IXGRP) or bits & stat.S_IXOTH
                    if not traversable:
                        errors.append("Key parent directory is not traversable by the API user")
                        break
            try:
                data = json.loads(key.read_text(encoding="utf-8"))
                if (data.get("type") != "service_account" or data.get("project_id") != project
                    or data.get("token_uri") != "https://oauth2.googleapis.com/token"
                    or data.get("universe_domain", "googleapis.com") != "googleapis.com"
                    or not str(data.get("client_email", "")).endswith(f"@{project}.iam.gserviceaccount.com")):
                    raise ValueError
                from google.oauth2.service_account import Credentials
                from google.auth.transport.requests import Request  # noqa: F401
                Credentials.from_service_account_info(data, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
            except Exception:
                errors.append("FCM key/dependencies are invalid or do not match the project; details suppressed")
    return {"configuration_valid": not errors, "email_configured": email, "fcm_enabled": push,
            "external_delivery_verified": False, "network_requests_performed": False,
            "errors": errors, "warnings": warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--require-email", action="store_true")
    parser.add_argument("--require-push", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.config.is_file() or args.config.stat().st_size > 65536:
            raise ValueError("Missing or oversized private configuration")
        from dotenv import dotenv_values
        result = check(dict(dotenv_values(args.config, interpolate=False)),
                       require_email=args.require_email, require_push=args.require_push)
    except Exception:
        result = {"configuration_valid": False, "errors": ["Configuration is unreadable or the service account is unavailable; details suppressed"]}
    print(json.dumps(result))
    return int(not result["configuration_valid"])


if __name__ == "__main__":
    raise SystemExit(main())
