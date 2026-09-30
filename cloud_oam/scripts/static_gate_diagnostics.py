"""Opt-in pytest progress/resource evidence; never changes test selection/results.

Only node IDs, phase results, durations and OS resource counters are recorded.
No environment variables, request payloads, credentials or test locals.
"""
import json
import os
from pathlib import Path
import resource
import sys
import time


def resources():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    value = {'peak_rss_bytes': int(usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024)),
             'cpu_user_seconds': usage.ru_utime, 'cpu_system_seconds': usage.ru_stime}
    for filename in ('memory.current', 'memory.peak', 'memory.max', 'memory.events'):
        path = Path('/sys/fs/cgroup')/filename
        try:
            content = path.read_text().strip()
            if filename == 'memory.events':
                value[filename] = {k:int(v) for k,v in (line.split() for line in content.splitlines())}
            else:
                value[filename] = int(content) if content != 'max' else 'max'
        except (OSError, ValueError):
            pass
    return value


def pytest_addoption(parser):
    parser.addoption('--static-diagnostics', default=None, help='Append static gate progress as JSONL')


def pytest_configure(config):
    target = config.getoption('--static-diagnostics')
    if target:
        config.pluginmanager.register(Diagnostics(Path(target)), 'rsc-static-diagnostics-writer')


class Diagnostics:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse to append a second run to stale evidence.
        self.stream = path.open('x', encoding='utf-8', buffering=1)
        self.start = time.monotonic()
        self.emit('session_start', pid=os.getpid())

    def emit(self, event, **fields):
        encoded=json.dumps(dict(event=event, elapsed_seconds=time.monotonic()-self.start,
            resources=resources(), **fields), ensure_ascii=True)
        self.stream.write(encoded+'\n')
        self.stream.flush()
        # A runner shutdown can prevent artifact upload. Send progress through
        # the original stdout too, bypassing pytest's normal test capture.
        if event in ('session_start','collection','test_start','session_finish'):
            print('STATIC_GATE_PROGRESS '+encoded, file=sys.__stdout__, flush=True)

    def pytest_collection_finish(self, session):
        self.emit('collection', count=len(session.items))

    def pytest_runtest_logstart(self, nodeid, location):
        self.emit('test_start', nodeid=nodeid)

    def pytest_runtest_logreport(self, report):
        self.emit('test_report', nodeid=report.nodeid, phase=report.when,
            outcome=report.outcome, duration_seconds=report.duration)

    def pytest_sessionfinish(self, session, exitstatus):
        self.emit('session_finish', exit_code=int(exitstatus), failed=session.testsfailed)
        self.stream.close()
