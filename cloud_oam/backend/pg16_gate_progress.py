"""Credential-free phase timings for the opt-in PostgreSQL release gate."""
import json
import re
import time


def run_gate_phase(label, operation, /, *args, **kwargs):
    """Report only a source-code label and elapsed time; preserve the result/error."""
    if not isinstance(label, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", label) is None:
        raise ValueError("gate phase label must be a static Python identifier")
    started = time.monotonic()
    print(json.dumps({"pg16_phase": label, "state": "started"}), flush=True)
    try:
        result = operation(*args, **kwargs)
    except BaseException:
        print(json.dumps({"pg16_phase": label, "state": "failed",
                          "elapsed_seconds": round(time.monotonic() - started, 3)}), flush=True)
        raise
    print(json.dumps({"pg16_phase": label, "state": "passed",
                      "elapsed_seconds": round(time.monotonic() - started, 3)}), flush=True)
    return result
