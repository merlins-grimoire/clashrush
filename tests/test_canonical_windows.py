"""Offline native proof: disposable interpreters, off-screen synthetic HWNDs."""
from dataclasses import replace
from contextlib import closing
import json
import secrets
from pathlib import Path
import subprocess
import sys
import time

import pytest

from clash_rush_rebuild.mvp_calibration import WindowObservation
from clash_rush_rebuild.mvp_frozen_stop import FrozenStopFacts
from clash_rush_rebuild.mvp_process_topology import ParentProcessTopology, ReleaseStageReceipt
from clash_rush_rebuild.win32_lifecycle_host import NativeLifecycleApi, Win32LifecycleHost
from clash_rush_rebuild.win32_runtime import NativeWin32Api, Win32Runtime
from test_policy_process_topology import ack_for


def wait_file(path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.is_file():
            try: return json.loads(path.read_text())
            except (ValueError, PermissionError): pass
        time.sleep(.02)
    pytest.fail('disposable window peer did not become ready')


@pytest.mark.parametrize('wrong_render', [False, True])
def test_native_distinct_render_retained_through_parent_pin_and_stop(tmp_path, monkeypatch, wrong_render):
    from surrogate_creation import permit_disposable_interpreter
    from clash_rush_rebuild import mvp_canonical_runtime
    monkeypatch.setattr(mvp_canonical_runtime, 'consume_native_creation', permit_disposable_interpreter)
    host = Win32LifecycleHost(NativeLifecycleApi(), nonce_factory=lambda:secrets.token_hex(16))
    topology = ParentProcessTopology(host, run_nonce='c'*32, generation=1, purpose='CALIBRATION_READ_ONLY')
    helper = Path(__file__).parent / 'support/canonical_window_peer.py'
    title = 'ClashRush disposable window proof'
    try:
        exe = sys._base_executable  # Do not make a venv redirector the HWND owner.
        offer = topology.prepare(worker_executable=exe,
            worker_command_line=subprocess.list2cmdline([exe,'-c','import time;time.sleep(45)']),
            player_executable=exe,
            player_command_line=subprocess.list2cmdline([exe,str(helper),'root',str(tmp_path)]))
        topology.start_worker(); topology.accept_worker_ack(ack_for(offer))
        wait_file(tmp_path / 'root.json'); wait_file(tmp_path / 'render.json')
        with closing(host.stable_job_members(topology.player_job)) as members:
            binding = host.bind_exact(offer.player, title, members)
            assert binding is not None and binding.render_identity != offer.player
            window = WindowObservation(offer.player,binding.root_hwnd,binding.render_hwnd,
                binding.width,binding.height,True,binding.render_identity,binding.capture_nonce)
        if wrong_render:
            with pytest.raises(RuntimeError):
                topology.pin_capture(replace(window,render=offer.player),title)
            assert host.wait_process(topology.player.process_handle,0) is False
            return
        topology.pin_capture(window,title)
        assert topology._captured_window == window and not topology._capture_members._closed
        token = topology.revoke('disposable proof')
        with closing(host.stable_job_members(topology.worker_job)) as members:
            receipt = ReleaseStageReceipt(offer,token,'NOT_REQUIRED',members.identities,True)
        topology.terminate_worker(receipt)
        assert host.wait_process(topology.worker.process_handle,5000)
        deadline = time.monotonic_ns()+5_000_000_000
        result = topology.retire_normal_player(expected_title=title,deadline=deadline/1e9,
            before_retire=lambda b:FrozenStopFacts('a'*64,1,deadline,time.time_ns()+5_000_000_000))
        assert result['windows_invalid'] and result['job_active'] == 0
    finally:
        # Harness-only retained unnamed Job cleanup; never product recovery.
        for job in (topology.worker_job,topology.player_job):
            if job is not None:
                host.terminate_job(job)
        for process in (topology.worker,topology.player):
            if process is not None:
                assert host.wait_process(process.process_handle,5000)
        topology.dispose()


def test_two_parent_processes_share_durable_anchor_after_mutex_release(tmp_path):
    from clash_rush_rebuild.mvp_session_authority import canonical_v3
    (tmp_path/'var').mkdir()
    (tmp_path/'var/occupancy.json').write_bytes(canonical_v3(dict(schema=1,installation='a'*64,
        phase='STOPPED',run=None,parent=None,worker=None,player=None,closure=None)))
    peer = Path(__file__).parent/'support/canonical_anchor_peer.py'
    processes = [subprocess.Popen([sys.executable,str(peer),str(tmp_path),name],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,close_fds=True)
                 for name in ('first','second')]
    try:
        end = time.monotonic()+10
        while not all((tmp_path/(name+'.ready')).exists() for name in ('first','second')):
            assert time.monotonic() < end
            time.sleep(.01)
        (tmp_path/'start').write_text('start')
        outputs = [p.communicate(timeout=15) for p in processes]
        assert all(p.returncode == 0 for p in processes), outputs
        assert sorted(out.strip() for out,err in outputs) == ['BLOCKED_NONTERMINAL','RESERVED']
        assert all(not err for out,err in outputs)
        assert json.loads((tmp_path/'var/occupancy.json').read_bytes())['phase'] == 'PREPARING'
    finally:
        for process in processes:
            if process.poll() is None: process.terminate()
            process.wait(timeout=5)
