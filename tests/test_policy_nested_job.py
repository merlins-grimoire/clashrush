"""Owner comment 178: real dedicated-Job proofs, disposable processes only.

These are OS topology checks, not finalization/input acceptance. No real input,
BlueStacks discovery, game process, capture or transport is used.
"""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from clash_rush_rebuild.win32_runtime import (
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, NativeWin32Api, Win32Runtime,
)

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='real Windows Jobs required')


def kernel_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, args, result in (
        ('QueryInformationJobObject', [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
        ('GetCurrentProcess', [], w.HANDLE),
        ('OpenProcess', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ('GetProcessId', [w.HANDLE], w.DWORD),
        ('CreateEventW', [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE),
    ):
        function = getattr(kernel, name)
        function.argtypes = args
        function.restype = result
    return kernel


def members(kernel, job):
    # Bounded fixture: at most the worker and one descendant. A truncated or
    # changing list fails instead of authorizing termination from a partial set.
    buffer = ctypes.create_string_buffer(8 + ctypes.sizeof(ctypes.c_size_t) * 8)
    if not kernel.QueryInformationJobObject(job, 3, buffer, len(buffer), None):
        raise ctypes.WinError(ctypes.get_last_error())
    assigned, listed = (w.DWORD.from_buffer(buffer, offset).value for offset in (0, 4))
    assert assigned == listed <= 8
    return tuple(ctypes.c_size_t.from_buffer(buffer, 8 + ctypes.sizeof(ctypes.c_size_t) * i).value
                 for i in range(listed))


def identity_matches(runtime, kernel, handle, expected):
    return (int(kernel.GetProcessId(handle)), runtime.process_creation_time(handle)) == expected


def spawn(runtime, arguments):
    # Windows venv python.exe is a redirector that creates another process.
    # Use the existing base interpreter, not an assumed one-process launcher.
    executable = sys._base_executable
    actor = runtime.create_suspended(executable, subprocess.list2cmdline([executable, *arguments]))
    return actor, (actor.pid, runtime.process_creation_time(actor.process_handle))


def cleanup(runtime, actors, jobs, handles):
    # Retained exact handles, never executable-name or numeric-PID termination.
    errors = []
    for actor, identity in actors:
        try:
            assert runtime.process_creation_time(actor.process_handle) == identity[1]
            if not runtime.wait_process(actor.process_handle, 0):
                try:
                    runtime.terminate_process(actor.process_handle)
                except BaseException:
                    # A concurrent Job kill can win between wait and terminate.
                    assert runtime.wait_process(actor.process_handle, 5000)
            assert runtime.wait_process(actor.process_handle, 5000)
        except BaseException as error:
            errors.append(type(error).__name__)
        handles.extend((actor.thread_handle, actor.process_handle))
    for handle in [*jobs, *handles]:
        try:
            runtime.close_handle(handle)
        except BaseException as error:
            errors.append(type(error).__name__)
    assert not errors, errors


@pytest.mark.parametrize('retirement', ['last_close', 'terminate_job'])
def test_dedicated_nested_job_isolates_exact_worker_and_witness(retirement):
    runtime = Win32Runtime(NativeWin32Api())
    kernel = kernel_api()
    actors, jobs = [], []
    try:
        worker, worker_id = spawn(runtime, ['-c', 'import time; time.sleep(30)'])
        actors.append((worker, worker_id))
        witness, witness_id = spawn(runtime, ['-c', 'import time; time.sleep(30)'])
        actors.append((witness, witness_id))
        ambient = runtime.is_process_in_job(kernel.GetCurrentProcess(), None)
        job = runtime.create_job()
        jobs.append(job)
        runtime.set_kill_on_close(job)
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        assert kernel.QueryInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits), None)
        assert limits.BasicLimitInformation.LimitFlags == 0x2000
        runtime.assign_to_job(job, worker.process_handle)
        assert runtime.is_process_in_job(worker.process_handle, job) is True
        assert runtime.is_process_in_job(witness.process_handle, job) is False
        assert runtime.is_process_in_job(kernel.GetCurrentProcess(), job) is False
        assert members(kernel, job) == members(kernel, job) == (worker.pid,)
        assert identity_matches(runtime, kernel, worker.process_handle, worker_id)
        assert not identity_matches(runtime, kernel, worker.process_handle, (worker.pid, worker_id[1] + 1))
        assert runtime.resume_thread(worker.thread_handle) == 1
        assert runtime.resume_thread(witness.thread_handle) == 1
        if retirement == 'last_close':
            runtime.close_handle(job)
            jobs.remove(job)
        else:
            runtime.terminate_job(job)
        assert runtime.wait_process(worker.process_handle, 5000)
        assert not runtime.wait_process(witness.process_handle, 0)
        assert identity_matches(runtime, kernel, witness.process_handle, witness_id)
        print(json.dumps({'retirement': retirement, 'ambient_member': ambient,
                          'dedicated_exact_members': 1, 'stale_identity_rejected': True,
                          'worker_terminated': True, 'witness_survived': True,
                          'parent_survived': True}))
    finally:
        cleanup(runtime, actors, jobs, [])
        print('dedicated proof: all disposable actors waited and handles closed')


def test_worker_created_descendant_inherits_outer_job_despite_separate_inner_job(tmp_path):
    """Tests the real old W->E spawn shape, not just a sibling stand-in."""
    runtime = Win32Runtime(NativeWin32Api())
    kernel = kernel_api()
    actors, jobs, handles = [], [], []
    descendant = None
    try:
        event_name = 'Local\\ClashRush-test-' + uuid.uuid4().hex
        event = kernel.CreateEventW(None, True, False, event_name)
        assert event
        handles.append(event)
        handoff = tmp_path / 'disposable-child.json'
        worker, worker_id = spawn(runtime, [str(Path(__file__).parent / 'support' / 'policy_process_peer.py'),
                                           str(handoff), event_name])
        actors.append((worker, worker_id))
        witness, witness_id = spawn(runtime, ['-c', 'import time; time.sleep(30)'])
        actors.append((witness, witness_id))
        job = runtime.create_job()
        jobs.append(job)
        runtime.set_kill_on_close(job)
        runtime.assign_to_job(job, worker.process_handle)
        assert runtime.resume_thread(worker.thread_handle) == 1
        assert runtime.resume_thread(witness.thread_handle) == 1
        # Event/handle wait is the readiness oracle, not a sleep.
        assert runtime.wait_process(event, 10000)
        record = json.loads(handoff.read_text(encoding='ascii'))
        descendant = kernel.OpenProcess(0x100000 | 0x1000 | 0x0100 | 0x0001, False, record['pid'])
        assert descendant
        handles.append(descendant)
        descendant_id = (record['pid'], record['creation'])
        assert identity_matches(runtime, kernel, descendant, descendant_id)
        inner = runtime.create_job()
        jobs.append(inner)
        runtime.assign_to_job(inner, descendant)
        assert runtime.is_process_in_job(descendant, inner) is True
        assert runtime.is_process_in_job(descendant, job) is True
        assert set(members(kernel, job)) == {worker.pid, record['pid']}
        assert members(kernel, inner) == (record['pid'],)
        assert not runtime.is_process_in_job(witness.process_handle, job)
        assert identity_matches(runtime, kernel, worker.process_handle, worker_id)
        # Test-only kill: ALL members are positively identified disposable actors.
        runtime.terminate_job(job)
        assert runtime.wait_process(worker.process_handle, 5000)
        assert runtime.wait_process(descendant, 5000)
        assert not runtime.wait_process(witness.process_handle, 0)
        assert identity_matches(runtime, kernel, witness.process_handle, witness_id)
        print(json.dumps({'worker_spawned_descendant': True, 'descendant_in_inner_job': True,
                          'descendant_still_in_worker_job': True, 'outer_members': 2,
                          'outer_kill_terminated_both_test_actors': True, 'witness_survived': True}))
    finally:
        # The dedicated test Job owns the descendant from creation, even when
        # publication fails. No ambient Job handle is ever queried or closed.
        for job in jobs[:1]:
            runtime.terminate_job(job)
        if descendant:
            assert runtime.wait_process(descendant, 5000)
        cleanup(runtime, actors, jobs, handles)
        print('inherited-child proof: all disposable actors waited and handles closed')
