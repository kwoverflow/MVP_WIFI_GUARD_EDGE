"""Read-only evidence from the explicitly enabled loopback PC fixture.

Requires GUI calibration and at least one real model inference first. Does not
change scenarios/settings, inject decisions, or send external notifications.
"""
import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, ProxyHandler, build_opener

from wifiguard_local_edge.metrics import atomic_json
from wifiguard_local_edge.pc_demo import DEFAULT_DIRECTORY, EMAIL, PASSWORD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe-seconds", type=int, default=5, choices=range(1, 46))
    parser.add_argument("--output", type=Path, default=DEFAULT_DIRECTORY / "verification.json")
    args = parser.parse_args()
    opener = build_opener(ProxyHandler({}))
    token = None
    def request(route, data=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = Request("http://127.0.0.1:8095" + route, None if data is None else json.dumps(data).encode(), headers)
        with opener.open(req, timeout=5) as response:
            return json.load(response)
    token = request("/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})["accessToken"]
    observations = []
    for _ in range(args.observe_seconds * 2):
        status = request("/api/v1/local-edge/status")
        assert status.get("simulation", {}).get("enabled"), "not a PC fixture"
        runtime = status.get("runtime") or {}
        gate = runtime.get("pi5_services", {}).get("inference_gate", {})
        observations.append({"at_unix": time.time(), "gate": gate.get("reason"),
                             "open": gate.get("open"), "grace_seconds": gate.get("grace_remaining_seconds"),
                             "input_current": runtime.get("input_current"),
                             "inferences": runtime.get("counters", {}).get("inferences")})
        time.sleep(.5)
    config = runtime.get("pi5_services", {}).get("runtime_configuration", {})
    simulation = status["simulation"]
    health = request("/health")
    ingest = health.get("ingest") or {}
    checks = {
        "runtime_fresh": status["runtime_current"],
        "unchanged_calibration_completed": status.get("calibration", {}).get("phase") == "done",
        "real_fp32_model_not_mocked": runtime.get("backend") == "onnx" and simulation["model_mocked"] is False,
        "real_inference_observed": runtime.get("counters", {}).get("inferences", 0) > 0,
        "settings_revision_applied": config.get("applied_revision") == status.get("desired_revision"),
        "presence_rows_written": ingest.get("presence_sink", {}).get("written", 0) > 0,
        "whole_service_resources_measured": status.get("resources", {}).get("complete") is True,
        "model_stride_half_second": runtime.get("stride_seconds") == .5,
    }
    result = {"kind": "PC software E2E evidence; NOT physical Pi5 performance or fall accuracy",
              "at_unix": time.time(), "checks": checks, "passed": all(checks.values()),
              "observations": observations, "model_version": runtime.get("model_version"),
              "counters": runtime.get("counters"), "stage_ms": runtime.get("stage_ms"),
              "processing_ms": runtime.get("processing_ms"), "resources": status.get("resources"),
              "local_notification": simulation["notification"],
              "db_presence_rows_this_api_run": ingest.get("presence_sink", {}).get("written"),
              "external_delivery_tested": False, "physical_pi5_tested": False}
    atomic_json(args.output, result)
    print(json.dumps({"passed": result["passed"], "checks": checks, "report": str(args.output.resolve()),
                      "observed_gate_states": sorted({v["gate"] for v in observations if v["gate"]})}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
