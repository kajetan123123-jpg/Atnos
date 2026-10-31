"""Observe a worker's coordinator identity; never kill, resume or retry work."""
import math
import os
import socket

import psutil


def current_executor_owner() -> dict:
    return {'host': socket.gethostname(), 'pid': os.getpid(),
            'process_started_at': psutil.Process().create_time()}


def execution_liveness(task, latest_run) -> dict:
    if task.status != 'running' or getattr(task, 'children', None):
        return {'state': 'not_applicable', 'execution_verified': False}
    metadata = getattr(latest_run, 'metadata_json', None) or {}
    owner = metadata.get('executor_owner')
    unknown = {'state':'unknown', 'execution_verified':False,
               'reason':'Brak dowodu aktywnego wykonawcy. Wynik wymaga uzgodnienia; nie ponawiaj automatycznie.'}
    if not isinstance(owner, dict) or owner.get('host') != socket.gethostname():
        return unknown
    pid, started = owner.get('pid'), owner.get('process_started_at')
    if type(pid) is not int or pid <= 0 or type(started) not in {int,float} or not math.isfinite(started):
        return unknown
    try:
        actual = psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        actual = None
    except (psutil.AccessDenied, OSError):
        return unknown
    if actual is None or abs(actual-started) > 0.01:
        return {'state':'owner_lost', 'execution_verified':False,
                'reason':'Proces wykonawcy zakończył się lub PID został użyty ponownie. Wynik jest niepotwierdzony; nie ponawiaj automatycznie.'}
    # A living API process alone does not prove that a particular coroutine is making progress.
    return {'state':'coordinator_alive', 'execution_verified':False}
