"""Spawn-importable synthetic processes; no app setup, network or credentials."""
import os
from pathlib import Path
import time


def hung_source(payload, expires):
    Path(payload['marker']).write_text(str(os.getpid()))
    if 'committed' in payload:
        Path(payload['committed']).write_text('awaiting_confirmation')
    while True:
        time.sleep(.1)


def healthy(payload, expires):
    return {'job_id': payload['job_id'], 'status': 'awaiting_confirmation', 'recovered': False}
