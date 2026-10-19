"""Comment 181 topology feasibility: ONLY disposable Windows actors.

These tests do not stand in for production composition/finalization acceptance.
P owns creation and final cleanup. W receives only query/synchronize access.
"""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path

import pytest

from test_policy_nested_job import cleanup, identity_matches, kernel_api, members, spawn
from clash_rush_rebuild.win32_runtime import (
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, NativeWin32Api, Win32Runtime,
)

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='real Windows Jobs required')
QUERY_AND_WAIT = 0x00101000


@pytest.mark.parametrize('case', [
    'close', 'terminate', 'stale_identity', 'duplicate_failure',
    'je_assignment_failure', 'wrong_membership', 'je_last_close',
])
def test_parent_created_suspended_player_handoff(tmp_path, case):
    runtime = Win32Runtime(NativeWin32Api())
    kernel = kernel_api()
    kernel.DuplicateHandle.argtypes = [w.HANDLE, w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE),
                                      w.DWORD, w.BOOL, w.DWORD]
    kernel.DuplicateHandle.restype = w.BOOL
    actors, jobs, handles = [], [], []
    import uuid
    event_name = 'Local\\ClashRush-test-' + uuid.uuid4().hex
    event = kernel.CreateEventW(None, True, False, event_name)
    assert event
    handles.append(event)
    remote = w.HANDLE()
    descendant = None
    player_job = None
    try:
        request, response = tmp_path / 'request.json', tmp_path / 'response.json'
        peer = Path(__file__).parent / 'support' / 'policy_handoff_peer.py'
        worker, worker_id = spawn(runtime, [str(peer), str(request), str(response), event_name])
        actors.append((worker, worker_id))
        player, player_id = spawn(runtime, ['-c', 'import time; time.sleep(30)'])
        actors.append((player, player_id))
        job = runtime.create_job()
        jobs.append(job)
        runtime.set_kill_on_close(job)
        runtime.assign_to_job(job, worker.process_handle)
        assert runtime.is_process_in_job(worker.process_handle, job)
        assert not runtime.is_process_in_job(kernel.GetCurrentProcess(), job)
        assert not runtime.is_process_in_job(player.process_handle, job)
        assert identity_matches(runtime, kernel, worker.process_handle, worker_id)
        assert identity_matches(runtime, kernel, player.process_handle, player_id)
        # Owner clarification 185: Je belongs to P and has NO killing limits.
        player_job = runtime.create_job()
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        assert kernel.QueryInformationJobObject(player_job, 9, ctypes.byref(limits),
                                                ctypes.sizeof(limits), None)
        assert limits.BasicLimitInformation.LimitFlags == 0
        if case == 'je_assignment_failure':
            with pytest.raises(Exception):
                runtime.assign_to_job(0, player.process_handle)
            assert not response.exists()
            print('invalid Je assignment: player remains suspended, no handoff or resume')
            return
        runtime.assign_to_job(player_job, player.process_handle)
        assert runtime.is_process_in_job(player.process_handle, player_job)
        assert members(kernel, player_job) == members(kernel, player_job) == (player.pid,)
        if case == 'wrong_membership':
            assert not runtime.is_process_in_job(player.process_handle, job)
            assert not response.exists()
            print('incorrect Je handle rejected by specific membership before resume')
            return
        source = 0 if case == 'duplicate_failure' else player.process_handle
        duplicated = bool(kernel.DuplicateHandle(kernel.GetCurrentProcess(), source,
                                                worker.process_handle, ctypes.byref(remote),
                                                QUERY_AND_WAIT, False, 0))
        if case == 'duplicate_failure':
            assert not duplicated and ctypes.get_last_error() == 6
            assert not response.exists()
            assert not runtime.wait_process(player.process_handle, 0)
            print('duplicate failure: no worker/player resume attempted')
            return
        assert duplicated
        expected = list(player_id)
        if case == 'stale_identity':
            expected[1] += 1
        request.write_text(json.dumps({'handle': remote.value, 'identity': expected,
                                       'descendant': case in ('close', 'terminate', 'je_last_close')}), encoding='ascii')
        assert runtime.resume_thread(worker.thread_handle) == 1
        assert runtime.wait_process(event, 10000)
        receipt = json.loads(response.read_text(encoding='ascii'))
        assert receipt['duplicate_closed'] is True
        assert receipt['identity'] == list(player_id)
        assert receipt['noninheritable'] and receipt['terminate_denied']
        if case == 'stale_identity':
            assert receipt['accepted'] is False
            assert not runtime.wait_process(player.process_handle, 0)
            print('stale creation identity: handoff rejected, no player resume attempted')
            return
        assert receipt['accepted'] is True
        child_id = tuple(receipt['descendant'])
        descendant = kernel.OpenProcess(QUERY_AND_WAIT, False, child_id[0])
        assert descendant
        handles.append(descendant)
        assert identity_matches(runtime, kernel, descendant, child_id)
        assert runtime.is_process_in_job(descendant, job)
        assert set(members(kernel, job)) == set(members(kernel, job)) == {worker.pid, child_id[0]}
        # Last check before resume: both original identities, exact exclusion,
        # worker-owned descendants, acknowledgment, and no inheritance confirmed.
        assert identity_matches(runtime, kernel, player.process_handle, player_id)
        assert not runtime.is_process_in_job(player.process_handle, job)
        assert not runtime.wait_process(worker.process_handle, 0)
        assert runtime.resume_thread(player.thread_handle) == 1
        if case == 'close':
            runtime.close_handle(job)
            jobs.remove(job)
        else:
            runtime.terminate_job(job)
        assert runtime.wait_process(worker.process_handle, 5000)
        assert runtime.wait_process(descendant, 5000)
        assert not runtime.wait_process(player.process_handle, 0)
        assert identity_matches(runtime, kernel, player.process_handle, player_id)
        assert runtime.is_process_in_job(player.process_handle, player_job)
        assert members(kernel, player_job) == (player.pid,)
        if case == 'je_last_close':
            # Test-only semantic proof, never a production containment action.
            runtime.close_handle(player_job)
            player_job = None
            assert not runtime.wait_process(player.process_handle, 0)
        print(json.dumps({'case': case, 'exact_worker_members': 2, 'player_excluded': True,
                          'minimal_duplicate_mask': QUERY_AND_WAIT, 'worker_and_child_terminated': True,
                          'player_survived': True, 'je_non_killing': True,
                          'parent_cleanup_owner_survived': True}))
    finally:
        # All Job members are test-created; cleanup remains controller-only.
        for job in jobs:
            runtime.terminate_job(job)
        if descendant:
            assert runtime.wait_process(descendant, 5000)
        if player_job is not None:
            handles.append(player_job)
        cleanup(runtime, actors, jobs, handles)
        print('handoff proof: all disposable actors waited and local handles closed')
