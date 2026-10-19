"""Synthetic codec cases + actual disposable Windows pipe/process proofs only."""
from dataclasses import replace, FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

import pytest

from clash_rush_rebuild.lifecycle import ProcessIdentity
from clash_rush_rebuild.mvp_process_topology import PlayerHandoff, HandoffAck, ReleaseStageReceipt
from clash_rush_rebuild.mvp_policy_channel import (
    PolicyContext, PolicyCodec, PolicyChannel, PolicyChannelError,
    WindowsPolicyListener, PlayerReady, Revoke, OutcomeNotice, MAX_FRAME,
)
from test_policy_finalization_v3 import binding_v3


def context(parent=ProcessIdentity(1, 1), worker=ProcessIdentity(2, 2), manifest=b'synthetic-manifest'):
    run = replace(binding_v3(), runner_manifest_digest=hashlib.sha256(manifest).hexdigest())
    offer = PlayerHandoff(run.run_nonce, run.generation, 'a' * 32, parent, worker,
                          ProcessIdentity(2147483646, 3), 11, 12)
    return PolicyContext(run, 1, offer, manifest)


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def pair():
    p, w, ctx = PolicyCodec('P'), PolicyCodec('W'), context()
    assert w.decode(p.encode(ctx)) == ctx
    return p, w, ctx


def test_immutable_context_and_complete_typed_roundtrip():
    p, w, ctx = pair()
    with pytest.raises(FrozenInstanceError):
        ctx.producer_epoch = 3
    ack = HandoffAck(ctx.offer, True, True)
    assert p.decode(w.encode(ack)) == ack
    revoke = Revoke('b' * 32)
    assert w.decode(p.encode(revoke)) == revoke
    release = ReleaseStageReceipt(ctx.offer, revoke.token, 'NO_AUTHORIZED_RELEASE', (ctx.offer.worker,), True)
    assert p.decode(w.encode(release)) == release
    outcome = OutcomeNotice('UNCERTAIN', 'INPUT_UNCERTAIN')
    assert p.decode(w.encode(outcome)) == outcome
    assert p.context.run == ctx.run and p.revocation == w.revocation == revoke.token


@pytest.mark.parametrize('fault', ['duplicate_key', 'extra_key', 'bool_seq', 'stale_generation',
    'stale_epoch', 'stale_challenge', 'stale_digest', 'manifest', 'direction', 'bad_json',
    'oversize', 'nan', 'nested', 'unknown_kind'])
def test_malformed_context_permanently_denied(fault):
    raw = PolicyCodec('P').encode(context())
    value = json.loads(raw)
    if fault == 'duplicate_key': raw = b'{"schema":1,' + raw[1:]
    elif fault == 'extra_key': value['extra'] = 'no'
    elif fault == 'bool_seq': value['sequence'] = True
    elif fault == 'stale_generation': value['payload']['run']['generation'] += 1
    elif fault == 'stale_epoch': value['payload']['producer_epoch'] += 1
    elif fault == 'stale_challenge': value['challenge'] = 'c' * 32
    elif fault == 'stale_digest': value['run_digest'] = 'd' * 64
    elif fault == 'manifest': value['payload']['runner_manifest_bytes'] = 'AAAA'
    elif fault == 'direction': value['sender'] = 'W'
    elif fault == 'bad_json': raw = b'not json: sensitive content'
    elif fault == 'oversize': raw = b' ' * (MAX_FRAME + 1)
    elif fault == 'nan': value['sequence'] = float('nan')
    elif fault == 'nested': raw = b'[' * 2000 + b']' * 2000
    elif fault == 'unknown_kind': value['kind'] = '__reduce__'
    if fault not in ('duplicate_key', 'bad_json', 'oversize', 'nested'):
        raw = wire(value)
    codec = PolicyCodec('W')
    with pytest.raises(PolicyChannelError, match='^POLICY_CHANNEL_PROTOCOL$'):
        codec.decode(raw)
    with pytest.raises(PolicyChannelError):
        codec.decode(PolicyCodec('P').encode(context()))
    assert codec.failed


@pytest.mark.parametrize('fault', ['replay', 'skip', 'reflection', 'wrong_offer', 'stale_run'])
def test_ack_replay_sequence_and_exact_run(fault):
    p, w, ctx = pair()
    raw = w.encode(HandoffAck(ctx.offer, True, True))
    value = json.loads(raw)
    if fault == 'replay': assert p.decode(raw).accepted
    elif fault == 'skip': value['sequence'] = 2
    elif fault == 'reflection': value['sender'] = 'P'
    elif fault == 'wrong_offer': value['payload']['offer']['remote_player_handle'] = 999
    elif fault == 'stale_run': value['run_digest'] = 'f' * 64
    with pytest.raises(PolicyChannelError): p.decode(wire(value))
    assert p.failed


@pytest.mark.parametrize('fault', ['ack_before_context', 'worker_context', 'parent_ack', 'release_before_revoke',
    'wrong_token', 'duplicate_member', 'missing_worker', 'boolean_member', 'nonbool_ack', 'outcome_before_ack'])
def test_typed_message_order_and_validation(fault):
    p, w, ctx = pair()
    sender = w
    if fault == 'ack_before_context':
        sender = PolicyCodec('W'); message = HandoffAck(ctx.offer, True, True)
    elif fault == 'worker_context': message = ctx
    elif fault == 'parent_ack': sender = p; message = HandoffAck(ctx.offer, True, True)
    elif fault == 'nonbool_ack': message = HandoffAck(ctx.offer, 1, True)
    elif fault == 'outcome_before_ack': message = OutcomeNotice('SUCCESS', 'PRE_D_COMPLETE')
    else:
        token = 'b' * 32
        if fault != 'release_before_revoke': w.decode(p.encode(Revoke(token)))
        members = (ctx.offer.worker,)
        if fault == 'wrong_token': token = 'c' * 32
        if fault == 'duplicate_member': members *= 2
        if fault == 'missing_worker': members = (ctx.offer.player,)
        if fault == 'boolean_member': members = (True,)
        message = ReleaseStageReceipt(ctx.offer, token, 'NOT_REQUIRED', members, True)
    with pytest.raises(PolicyChannelError): sender.encode(message)
    assert sender.failed


def test_revoke_can_cross_ack_but_never_clears_revocation():
    p, w, ctx = pair()
    ack = w.encode(HandoffAck(ctx.offer, True, True))
    revoke = p.encode(Revoke('b' * 32))
    assert p.decode(ack).accepted
    assert w.decode(revoke).token == 'b' * 32
    assert p.revocation == w.revocation == 'b' * 32


def test_worker_cannot_generate_fresh_ack_after_receiving_revoke():
    p, w, ctx = pair()
    w.decode(p.encode(Revoke('b' * 32)))
    with pytest.raises(PolicyChannelError): w.encode(HandoffAck(ctx.offer, True, True))


# The child does not consume a real reservation or use handoff handles. These
# messages prove IPC only, not gameplay, native admission, or lifecycle facts.
def child(name, parent_pid, creation, mode):
    parent = ProcessIdentity(int(parent_pid), int(creation))
    channel = PolicyChannel.connect(name, parent=parent, timeout=3)
    ctx = channel.context
    if mode == 'silent':
        print('AUTONOMOUS', flush=True)  # must not become an acknowledgment
        time.sleep(.7)
    elif mode == 'die':
        time.sleep(.05)
        os._exit(0)
    elif mode == 'close':
        channel.close()
        time.sleep(.5)  # pipe loss while retained peer is still alive
    elif mode == 'watch_close':
        channel.send(HandoffAck(ctx.offer, True, True))
        assert channel.receive(timeout=3) == PlayerReady(ctx.offer)
        end = time.monotonic() + 2
        while not channel.no_more_gameplay and time.monotonic() < end:
            time.sleep(.005)
        assert channel.no_more_gameplay and channel._closed
    elif mode in ('malformed', 'replay', 'stale', 'oversize', 'partial'):
        raw = PolicyCodec('W')
        # Prepare independent codec state for deliberate wire corruption only.
        raw.decode(PolicyCodec('P').encode(ctx))
        payload = raw.encode(HandoffAck(ctx.offer, True, True))
        if mode == 'malformed': payload = b'{"private":"do not echo"}'
        if mode == 'stale':
            value = json.loads(payload); value['challenge'] = '0' * 32; payload = wire(value)
        if mode in ('oversize', 'partial'):
            data = struct.pack('!I', MAX_FRAME + 1 if mode == 'oversize' else 100) + b'x'
            channel._pipe.transfer(data, len(data), time.monotonic() + 1)
        else:
            channel._pipe.send(payload, time.monotonic() + 1)
            if mode == 'replay': channel._pipe.send(payload, time.monotonic() + 1)
        time.sleep(.7)
    else:
        channel.send(HandoffAck(ctx.offer, True, True))
        assert channel.receive(timeout=3) == PlayerReady(ctx.offer)
        if mode == 'pending_revoke':
            end = time.monotonic() + 2
            while not channel.no_more_gameplay and time.monotonic() < end:
                time.sleep(.005)
            assert channel.no_more_gameplay and not channel._closed
        revoke = channel.receive(timeout=3)
        assert type(revoke) is Revoke and channel.no_more_gameplay
        channel.send(ReleaseStageReceipt(ctx.offer, revoke.token, 'NO_AUTHORIZED_RELEASE',
                                          (ctx.offer.worker,), True))
        channel.send(OutcomeNotice('UNCERTAIN', 'INPUT_UNCERTAIN'))
        # Keep the exact retained process live until P has read the final frame.
        try: channel.receive(timeout=2)
        except PolicyChannelError: pass
    channel.close()


@pytest.fixture
def native_peer():
    if os.name != 'nt': pytest.skip('real Windows pipe proof')
    listener = WindowsPolicyListener()
    children = []
    handles = []
    channels = []
    def start(mode='roundtrip', *, manifest=b'synthetic-manifest', wrong_creation=False, wrong_peer=False):
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join((str(Path(__file__).resolve().parents[1] / 'src'),
                                             str(Path(__file__).resolve().parent), *sys.path))
        # The venv redirector spawns a different PID; bind the actual interpreter.
        process = subprocess.Popen([sys._base_executable, str(Path(__file__).resolve()), '--child',
            listener.name, str(listener.parent.pid), str(listener.parent.creation_time_100ns), mode],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
        children.append(process)
        expected_process = process
        if wrong_peer:
            expected_process = subprocess.Popen([sys._base_executable, '-c', 'import time;time.sleep(1.5)'],
                                                  close_fds=True)
            children.append(expected_process)
        api = listener._pipe.api
        handle = api.open_process_for_identity(expected_process.pid)
        handles.append(handle)
        identity = listener._pipe.identify(handle)
        if wrong_creation: identity = replace(identity, creation_time_100ns=identity.creation_time_100ns + 1)
        ctx = context(listener.parent, identity, manifest)
        channel = listener.accept(worker_handle=handle, context=ctx, timeout=3)
        channels.append(channel)
        return channel, process
    yield start, listener
    for channel in channels: channel.close()
    listener.close()
    for handle in handles: listener._pipe.api.close_handle(handle)
    for process in children:
        try: process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()  # only this test-owned disposable actor
            process.communicate(timeout=3)
            pytest.fail('disposable child exceeded finite lifetime')


@pytest.mark.parametrize('mode', ['roundtrip', 'pending_revoke'])
def test_windows_disposable_roundtrip_large_manifest_and_no_inheritance(native_peer, mode):
    start, listener = native_peer
    channel, process = start(mode, manifest=b'synthetic-' * 20000)
    assert not channel._pipe.api.handle_is_inheritable(channel._pipe.handle)
    assert not channel._pipe.api.handle_is_inheritable(channel._pipe.peer)
    assert channel.context.offer.worker.pid == process.pid
    assert not channel.no_more_gameplay
    ack = channel.receive(timeout=3)
    assert type(ack) is HandoffAck and ack.offer == channel.context.offer
    channel.send(PlayerReady(channel.context.offer))
    channel.send(Revoke('b' * 32))
    assert channel.no_more_gameplay
    release = channel.receive(timeout=3)
    assert type(release) is ReleaseStageReceipt and release.worker_members == (channel.context.offer.worker,)
    assert channel.receive(timeout=3) == OutcomeNotice('UNCERTAIN', 'INPUT_UNCERTAIN')
    channel.close()
    out, err = process.communicate(timeout=3)
    assert process.returncode == 0, err.decode(errors='replace')
    assert out == err == b''  # stdout is neither a protocol nor permission source
    assert channel.no_more_gameplay


@pytest.mark.parametrize('mode', ['malformed', 'replay', 'stale', 'oversize', 'partial'])
def test_windows_bad_wire_closes_and_latches(native_peer, mode):
    channel, process = native_peer[0](mode)
    if mode == 'replay': assert type(channel.receive(timeout=.4)) is HandoffAck
    with pytest.raises(PolicyChannelError, match='^POLICY_CHANNEL_RECEIVE$'):
        channel.receive(timeout=.2)
    assert channel.no_more_gameplay and channel._closed
    with pytest.raises(PolicyChannelError): channel.send(Revoke('b' * 32))


def test_windows_receive_timeout_is_finite_and_not_retryable(native_peer):
    channel, process = native_peer[0]('silent')
    before = time.monotonic()
    with pytest.raises(PolicyChannelError): channel.receive(timeout=.05)
    assert time.monotonic() - before < .5
    assert channel.no_more_gameplay
    out, err = process.communicate(timeout=3)
    assert process.returncode == 0 and out.strip() == b'AUTONOMOUS' and err == b''


def test_windows_write_timeout_with_nonreading_peer_is_finite(native_peer):
    channel, process = native_peer[0]('silent')
    before = time.monotonic()
    # Test the concrete transport's backpressure, not a fake writer.
    with pytest.raises(PolicyChannelError):
        channel._pipe.send(b'x' * 500000, time.monotonic() + .05)
    with pytest.raises(PolicyChannelError): channel.send(Revoke('b' * 32), timeout=.05)
    assert time.monotonic() - before < .5 and channel.no_more_gameplay and channel._closed


@pytest.mark.parametrize('mode', ['die', 'close'])
def test_windows_peer_death_or_pipe_loss_latches_without_waiting_for_receive(native_peer, mode):
    channel, process = native_peer[0](mode)
    end = time.monotonic() + 2
    while not channel.no_more_gameplay and time.monotonic() < end:
        time.sleep(.005)
    assert channel.no_more_gameplay
    assert channel._closed


def test_windows_retained_creation_time_mismatch_denied(native_peer):
    with pytest.raises(PolicyChannelError, match='^POLICY_CHANNEL_ACCEPT$'):
        native_peer[0]('silent', wrong_creation=True)


def test_windows_worker_latches_parent_pipe_loss(native_peer):
    channel, process = native_peer[0]('watch_close')
    assert type(channel.receive(timeout=3)) is HandoffAck
    channel.send(PlayerReady(channel.context.offer))
    channel.close()
    out, err = process.communicate(timeout=3)
    assert process.returncode == 0, err.decode(errors='replace')
    assert out == err == b''


def test_windows_unrelated_live_client_pid_is_denied(native_peer):
    with pytest.raises(PolicyChannelError, match='^POLICY_CHANNEL_ACCEPT$'):
        native_peer[0]('silent', wrong_peer=True)


def test_windows_unrelated_live_server_pid_is_denied():
    if os.name != 'nt': pytest.skip('real Windows pipe proof')
    listener = WindowsPolicyListener()
    process = subprocess.Popen([sys._base_executable, '-c', 'import time;time.sleep(.5)'], close_fds=True)
    handle = listener._pipe.api.open_process_for_identity(process.pid)
    try:
        unrelated = listener._pipe.identify(handle)
        with pytest.raises(PolicyChannelError, match='^POLICY_CHANNEL_CONNECT$'):
            PolicyChannel.connect(listener.name, parent=unrelated, timeout=.2)
    finally:
        listener.close(); listener._pipe.api.close_handle(handle); process.wait(timeout=3)


@pytest.mark.parametrize('timeout', [0, -1, True, float('nan'), float('inf'), 31])
def test_windows_invalid_timeout_closes_even_before_io(native_peer, timeout):
    channel, process = native_peer[0]('silent')
    with pytest.raises(PolicyChannelError): channel.receive(timeout=timeout)
    assert channel.no_more_gameplay and channel._closed


def test_windows_wrong_server_creation_time_denied():
    if os.name != 'nt': pytest.skip('real Windows pipe proof')
    listener = WindowsPolicyListener()
    try:
        with pytest.raises(PolicyChannelError, match='^POLICY_CHANNEL_CONNECT$'):
            PolicyChannel.connect(listener.name, parent=replace(listener.parent,
                creation_time_100ns=listener.parent.creation_time_100ns + 1), timeout=.1)
    finally: listener.close()


def test_windows_accept_timeout_without_client():
    if os.name != 'nt': pytest.skip('real Windows pipe proof')
    listener = WindowsPolicyListener()
    process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(.4)'], close_fds=True)
    handle = listener._pipe.api.open_process_for_identity(process.pid)
    try:
        ctx = context(listener.parent, listener._pipe.identify(handle))
        before = time.monotonic()
        with pytest.raises(PolicyChannelError): listener.accept(worker_handle=handle, context=ctx, timeout=.05)
        assert time.monotonic() - before < .5
    finally:
        listener.close(); listener._pipe.api.close_handle(handle); process.wait(timeout=3)


if __name__ == '__main__':
    assert sys.argv[1] == '--child'
    child(*sys.argv[2:])
