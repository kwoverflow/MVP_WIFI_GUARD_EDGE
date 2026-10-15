"""Lightweight nonce confirmation, without loading numerical/model modules."""
import json
import os
import time
from pathlib import Path
from uuid import uuid4


def confirm_empty_room(output_dir):
    directory = Path(output_dir)
    state = json.loads((directory / "startup-calibration.json").read_text(encoding="utf-8"))
    if state["phase"] != "waiting_empty_room":
        raise ValueError("edge is not waiting for empty-room confirmation")
    target = directory / "empty-room-confirmation.json"
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump({"run_id": state["run_id"], "empty_room_confirmed": True,
                       "confirmed_at_unix": time.time()}, stream, allow_nan=False)
        temporary.chmod(0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return state["run_id"]
