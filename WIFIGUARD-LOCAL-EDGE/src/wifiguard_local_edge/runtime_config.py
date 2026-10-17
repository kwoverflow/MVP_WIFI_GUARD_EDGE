"""Hot settings at a model-tick boundary; original AB rules are NOT replaced."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace

from .control import read_json


class RuntimeConfiguration:
    def __init__(self, directory, device_id, tenant_id):
        self.path = directory / "runtime-config.json"
        self.device_id, self.tenant_id = str(device_id), tenant_id
        self.revision = None
        self.error = None
        self.effective = None

    def apply(self, presence, engine, trigger):
        value = read_json(self.path)
        if not value:
            if self.path.exists():
                self.error = "runtime config unreadable"
            return False
        if value.get("revision") == self.revision and self.error is None:
            return False
        try:
            if (value.get("device_id"), value.get("tenant_id")) != (self.device_id, self.tenant_id):
                raise ValueError("runtime config identity mismatch")
            payload = {key: value[key] for key in ("bound", "device_id", "tenant_id", "settings")}
            expected = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            if value["revision"] != expected:
                raise ValueError("runtime config checksum mismatch")
            settings = value["settings"]
            if settings is not None:
                bounds = {"presence_mv_threshold": (0, 10), "wander_ratio_threshold": (1, 5),
                          "presence_timeout_s": (0, 60), "threshold": (0, 1), "cooldown_seconds": (0, 60)}
                if set(settings) != set(bounds):
                    raise ValueError("unknown runtime setting")
                for key, (low, high) in bounds.items():
                    v = settings[key]
                    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
                        raise ValueError("invalid runtime setting")
                if min(settings["presence_mv_threshold"], settings["presence_timeout_s"]) <= 0:
                    raise ValueError("positive presence settings required")
                candidate = replace(presence.config, **{key: settings[key] for key in
                                    ("presence_mv_threshold", "wander_ratio_threshold", "presence_timeout_s")})
                presence.apply_config(candidate, value["revision"])
                # This is the legacy RAW probability threshold (IDLE/SUSPECT
                # and record metadata). Markov, EMA and B k-of-n are unchanged.
                engine.threshold = settings["threshold"]
                trigger.reset()
            self.revision, self.error = value["revision"], None
            self.effective = {"presence_mv_threshold": presence.config.presence_mv_threshold,
                              "wander_ratio_threshold": presence.config.wander_ratio_threshold,
                              "presence_timeout_s": presence.config.presence_timeout_s,
                              "threshold": engine.threshold,
                              "cooldown_seconds": (settings or {}).get("cooldown_seconds", 10),
                              "threshold_semantics": "raw_probability_only; original AB confirmation unchanged"}
            return True
        except (ValueError, KeyError, TypeError) as exc:
            self.error = str(exc)
            return False

    def status(self):
        return {"applied_revision": self.revision, "last_error": self.error, "effective": self.effective}
