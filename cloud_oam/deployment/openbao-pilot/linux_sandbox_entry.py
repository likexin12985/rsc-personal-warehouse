"""Run the synthetic harness only inside the reviewed disposable Linux sandbox.

This entry does not create a container or connect to Docker/the host. It inspects
its own kernel boundary before starting Bao. No production configuration, cloud
credentials or business volumes belong in this container.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import platform
import resource
import time


class SandboxBoundaryError(RuntimeError):
    pass


def require(condition):
    if not condition:
        raise SandboxBoundaryError('isolated_linux_boundary_rejected')


def verify_boundary():
    require(platform.system() == 'Linux' and os.geteuid() == os.getegid() == 65532)
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    require(status['NoNewPrivs'].strip() == '1' and status['Seccomp'].strip() == '2')
    require(all(int(status[name].strip(), 16) == 0 for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')))
    require(resource.getrlimit(resource.RLIMIT_CORE) == (0, 0))
    require({line.split(':', 1)[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()[2:]} == {'lo'})
    require(len(Path('/proc/net/route').read_text().splitlines()) <= 1)
    mounts = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    root = [row for row in mounts if row[4] == '/']
    temporary = [row for row in mounts if row[4] == '/tmp']
    require(len(root) == len(temporary) == 1 and 'ro' in root[0][5].split(','))
    tmp = temporary[0]
    require(tmp[tmp.index('-') + 1] == 'tmpfs' and {'rw', 'nosuid', 'nodev'} <= set(tmp[5].split(',')))
    group = Path('/sys/fs/cgroup')
    require((group/'memory.max').read_text().strip() == '536870912')
    require((group/'memory.swap.max').read_text().strip() == '0')
    quota, period = (group/'cpu.max').read_text().split()
    require(quota.isdigit() and period.isdigit() and int(quota)*2 == int(period))
    require((group/'pids.max').read_text().strip() == '256')
    require(not any(name.startswith(('BAO_', 'VAULT_', 'ALIBABA_', 'ALIBABACLOUD_', 'AWS_', 'OAM_')) for name in os.environ))
    return {'nonRootUid': 65532, 'noNewPrivileges': True, 'capabilitiesEmpty': True,
            'seccompFiltering': True, 'coreDumpDisabled': True, 'onlyLoopbackInterface': True,
            'rootReadOnly': True, 'stateTmpfs': True, 'memoryLimitBytes': 536870912,
            'swapLimitBytes': 0, 'cpuLimitCores': 0.5, 'pidLimit': 256}


def main():
    result = {'schema': 'rsc.openbao.linux-sandbox.v1', 'productionReady': False,
              'productionConfigured': False, 'syntheticOnly': True, 'childStarted': False}
    started = time.monotonic()
    try:
        result['boundary'] = verify_boundary()
        # Import only after the Linux boundary has passed. The caller supplies
        # the harness's explicit binary/hook arguments; none may contain tokens.
        from isolated_openbao_harness import main as run_harness
        output = io.StringIO()
        result['childStarted'] = True
        with contextlib.redirect_stdout(output):
            returncode = run_harness()
        encoded = output.getvalue()
        require(len(encoded) <= 65536)
        result['harness'] = json.loads(encoded)
        require(result['harness']['productionReady'] is False and result['harness']['syntheticOnly'] is True)
        result['result'] = 'passed' if returncode == 0 else 'failed'
    except Exception as error:
        result['result'] = 'failed'
        result['failure'] = 'boundary_rejected' if isinstance(error, SandboxBoundaryError) else 'prototype_failed'
    result['elapsedSeconds'] = round(time.monotonic() - started, 3)
    result['childrenPeakRssKiB'] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    peak = Path('/sys/fs/cgroup/memory.peak')
    if platform.system() == 'Linux' and peak.is_file():
        value = peak.read_text().strip()
        if value.isdigit():
            result['cgroupMemoryPeakBytes'] = int(value)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
