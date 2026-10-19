"""Disposable W exercising the production handoff receiver, no input/capture."""
import ctypes
from ctypes import wintypes as w
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

# Base interpreter avoids the Windows venv redirector spawning another W.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from clash_rush_rebuild.lifecycle import ProcessIdentity
from clash_rush_rebuild.mvp_process_topology import (
    PlayerHandoff, ReleaseStageReceipt, acknowledge_player_handoff,
)
from clash_rush_rebuild.win32_lifecycle_host import NativeProcessApi, Win32LifecycleHost


def main():
    request, response, release_request, release_response, ready_name, proceed_name, released_name = sys.argv[1:]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, args, result in (
        ('OpenEventW', [w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
        ('SetEvent', [w.HANDLE], w.BOOL),
        ('TerminateProcess', [w.HANDLE, w.UINT], w.BOOL),
        ('TerminateJobObject', [w.HANDLE, w.UINT], w.BOOL),
    ):
        method = getattr(kernel, name)
        method.argtypes, method.restype = args, result
    api = NativeProcessApi()
    host = Win32LifecycleHost(api, nonce_factory=lambda: '0' * 32)
    events = [kernel.OpenEventW(0x100002, False, n)
              for n in (ready_name, proceed_name, released_name)]
    assert all(events)
    handle = None
    query_job = None
    child = None
    try:
        record = json.loads(Path(request).read_text(encoding='ascii'))
        values = record['offer']
        for role in ('parent', 'worker', 'player'):
            values[role] = ProcessIdentity(**values[role])
        offer = PlayerHandoff(**values)
        handle = offer.remote_player_handle
        query_job=offer.remote_player_job_query
        if 'session' in record:
            from clash_rush_rebuild.mvp_deployment_worker import consume_policy_handoff
            from clash_rush_rebuild.mvp_session_authority import DurableSessionAuthority
            from clash_rush_rebuild.win32_runtime import Win32Runtime
            session=record['session']
            lease=Win32Runtime(api).acquire_mutex(name=session['mutex'])
            authority=DurableSessionAuthority(Path(session['database']))
            try:
                run=authority.read_run_v3(offer.generation)
                ack=consume_policy_handoff(authority=authority,run=run,producer_epoch=1,
                    offer=offer,host=host,mutex=lease,
                    runner_manifest_bytes=session['manifest'].encode('ascii'))
            finally:
                authority.close()
                lease.release()
        else:
            ack = acknowledge_player_handoff(host, offer)
        ctypes.set_last_error(0)
        assert not kernel.TerminateProcess(handle, 77)
        assert ctypes.get_last_error() == 5
        assert host.job_limit_flags(query_job)==0
        assert host.is_process_in_job(handle,query_job)
        with_query=host.stable_job_members(query_job)
        try:assert offer.player in with_query.identities
        finally:with_query.close()
        ctypes.set_last_error(0)
        assert not kernel.TerminateJobObject(query_job,77)
        assert ctypes.get_last_error()==5
        ctypes.set_last_error(0)
        assert not api.assign_process_to_job(query_job,host.current_process_handle())
        assert ctypes.get_last_error()==5
        Path(response).write_text(json.dumps({'ack': asdict(ack), 'terminate_denied': True,
            'job_mutation_denied':True}), encoding='ascii')
        assert kernel.SetEvent(events[0])
        if not host.wait_process(events[1], 20000):
            return
        release = json.loads(Path(release_request).read_text(encoding='ascii'))
        members = [offer.worker]
        if release['descendant']:
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'],
                                     close_fds=True, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            members.append(host.identity_from_handle(int(child._handle), child.pid))
        # No spawning after this point. This peer's receipt is a protocol test,
        # not evidence that real input cleanup ran.
        receipt = ReleaseStageReceipt(offer, release['revoke_token'], release['stage'], tuple(members), True)
        Path(release_response).write_text(json.dumps(asdict(receipt)), encoding='ascii')
        assert kernel.SetEvent(events[2])
        # Finite emergency lifetime; only event/handle waits are test oracles.
        host.wait_process(host.current_process_handle(), 30000)
    finally:
        if handle is not None:
            host.close_handle(handle)
        if query_job is not None:
            host.close_handle(query_job)
        for event in events:
            host.close_handle(event)
        if child is not None:
            # Test-owned only; normal test path retires the whole exact Jw.
            child.terminate()
            child.wait(timeout=5)


if __name__ == '__main__':
    main()
