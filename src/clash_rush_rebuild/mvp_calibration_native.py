"""Read-only native calibration: exact approved anchors, never gameplay assets.

PrintWindow/BGR uses the existing retained-family adapter. Deliberately supports
only HOME, native 1:1 geometry and exact narrow anchor bytes; other screens or
scaling need separately reviewed specifications, not a fallback recognizer.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import re
import time
import traceback

from .mvp_calibration import (CalibrationScope, CalibrationWindowView,
    CalibrationSources, WindowObservation, need, hex_value)
from .mvp_session_authority import canonical_v3, digest_v3

HOST_PROFILE = 'bec50bfe3c12866ea56012d26e01295eedfcdf1311ec7825b77e0b031cc77528'
CROP_IDS = ('HOME_ANCHOR', 'SOURCE_ANCHOR')


@dataclass(frozen=True, slots=True)
class CalibrationSpec:
    raw: bytes
    size: tuple
    crops: tuple

    @classmethod
    def decode(cls, raw, scope):
        need(type(raw) is bytes and 0 < len(raw) <= 262144)
        try:
            d = json.loads(raw)
            need(type(d) is dict and canonical_v3(d) == raw and set(d) == {
                'schema', 'source_tree', 'slot', 'slot_id', 'instance_digest',
                'slots_digest', 'configuration_digest', 'host_profile_digest',
                'host_witness_digest', 'ui_revision', 'calibration_version',
                'source_screen', 'capture_method', 'native_size', 'mapping', 'crops'})
            need(type(scope) is CalibrationScope and digest_v3(raw) == scope.profile_digest
                and d['source_tree'] == scope.candidate_tree
                and type(d['slot']) is int and d['slot'] == scope.slot
                and d['instance_digest'] == scope.instance_digest
                and type(d['schema']) is int and d['schema'] == 1
                and type(d['calibration_version']) is int and d['calibration_version'] > 0
                and type(d['slot_id']) is str and re.fullmatch(r'slot-[0-9]{3}', d['slot_id'])
                and type(d['ui_revision']) is str and re.fullmatch(r'[a-z0-9_-]{1,64}', d['ui_revision'])
                and all(hex_value(d[k],64) for k in ('slots_digest','configuration_digest','host_witness_digest'))
                and d['host_profile_digest'] == HOST_PROFILE
                and d['source_screen'] == 'HOME' and d['capture_method'] == 'PRINTWINDOW_BGR_NATIVE')
            size = d['native_size']
            need(type(size) is list and len(size) == 2
                and all(type(v) is int and 64 <= v <= 4096 for v in size))
            w,h = size
            need(type(d['mapping']) is list and all(type(v) is int for v in d['mapping'])
                and d['mapping'] == [0,0,w,h])
            crops = d['crops']
            need(type(crops) is list and len(crops) == 2)
            parsed = []
            for c, name in zip(crops,CROP_IDS):
                need(type(c) is dict and set(c) == {'id','box','template_digest','maximum_delta'}
                    and c['id'] == name and hex_value(c['template_digest'],64)
                    and type(c['maximum_delta']) is int and c['maximum_delta'] == 0)
                box = c['box']
                need(type(box) is list and len(box) == 4 and all(type(v) is int for v in box))
                x,y,r,b = box
                need(0 <= x < r <= w and 0 <= y < b <= h
                    and r-x <= 256 and b-y <= 128 and (r-x)*(b-y)*16 <= w*h)
                parsed.append((name,tuple(box),c['template_digest']))
            a,b = parsed[0][1],parsed[1][1]
            need(a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
            return cls(raw,tuple(size),tuple(parsed))
        except (ValueError,TypeError,KeyError):
            raise RuntimeError('CALIBRATION_SPECIFICATION_DENIED') from None


def write_protected_record(project, relative, payload):
    """Exclusive create, flush, close and independent readback; no raw frames.

    Retain non-delete-shared directory handles across create and readback. Never
    repair a pre-existing destination or overwrite an earlier/partial receipt.
    """
    from pathlib import Path
    from .mvp_policy_ingress import _existing_root, _require_private
    from .mvp_deployment_profile import _read_windows
    from .win32_state_io import NativeWin32StateApi
    from .approval_reconciliation import _seal_private_path
    need(type(relative) is str and (re.fullmatch(
        r'private/[a-z][a-z0-9_-]{0,63}/[0-9a-f]{32}\.json',relative)
        or relative in ('private/slots.json','private/deployment/runner-manifest.json',
                        'var/.policy-setup.pending')
        or re.fullmatch(r'var/approvals/[0-9a-f]{32}\.json',relative)))
    need(type(payload) is bytes and 0 < len(payload) <= 524288)
    root = _existing_root(project)
    path = root / relative
    api = NativeWin32StateApi()
    handles = []
    try:
        current = Path(root.anchor)
        for part in path.parent.parts[1:]:
            current = current / part
            handle = api.create_file(str(current),0x80000000,1,3,0x02200000)
            need(handle is not None)
            handles.append(handle)
            need(api.handle_attributes(handle) & 0x400 == 0
                and api.handle_attributes(handle) & 0x10 != 0
                and Path(api.final_path(handle)) == current)
        _require_private(path.parent)
        # Retain a metadata-only identity handle; DACL readers share READ only.
        # Sharing WRITE here permits our temporary data writer, never deletion.
        handle = api.create_file(str(path),0x00060000,3,1,0x00200000)
        need(handle is not None)
        handles.append(handle)
        need(api.handle_attributes(handle) & 0x410 == 0 and Path(api.final_path(handle)) == path)
        _seal_private_path(path,False)
        _require_private(path)
        handle = api.create_file(str(path),0x40000000,1,3,0x80200000)
        need(handle is not None)
        handles.append(handle)
        need(api.handle_attributes(handle) & 0x410 == 0 and Path(api.final_path(handle)) == path)
        need(api.write_file(handle,payload) == len(payload))
        need(api.flush_file_buffers(handle) is True)
        need(api.close_handle(handles[-1]) is True)
        handles.pop()
        _require_private(path)
        readback = _read_windows(root,tuple(relative.split('/')),524288)
        need(readback == payload)
        _require_private(path)
        return readback
    finally:
        closed = True
        for handle in reversed(handles):
            try: closed = api.close_handle(handle) is True and closed
            except BaseException: closed = False
        need(closed)


class NarrowEvidenceSink:
    def __init__(self, project, spec, *, write=write_protected_record):
        need(type(spec) is CalibrationSpec)
        self.project,self.spec,self.write = project,spec,write
        self._used = False

    def persist(self, scope, offer, window, crops, observed_ns):
        need(not self._used)
        self._used = True
        need(CalibrationSpec.decode(self.spec.raw,scope) == self.spec
            and type(crops) is dict and set(crops) == set(CROP_IDS)
            and type(window) is WindowObservation and window.supported_visible is True
            and (window.width,window.height) == self.spec.size
            and type(observed_ns) is int and scope.issued_ns <= observed_ns < scope.expires_ns)
        window.require_v2()
        for name,(x,y,r,b),expected in self.spec.crops:
            need(type(crops[name]) is bytes and len(crops[name]) == (r-x)*(b-y)*3
                and digest_v3(crops[name]) == expected)
        # Canonical hex BGR is intentionally boring: exact bounded narrow bytes,
        # no codec metadata, filenames from config, full-frame encoding or OCR.
        payload = canonical_v3(dict(schema=2,scope_digest=scope.digest,
            specification_digest=digest_v3(self.spec.raw),offer_digest=digest_v3(canonical_v3(asdict(offer))),
            window=asdict(window),observed_ns=observed_ns,
            crops={k:v.hex() for k,v in crops.items()}))
        readback = self.write(self.project,scope.private_root+'/'+scope.run_nonce+'.json',payload)
        need(type(readback) is bytes and readback == payload)
        return digest_v3(readback)


class CalibrationConfiguration:
    """Protected parent/worker source closure; never loads a gameplay provider.

    Host settings are an explicit fresh operator measurement bound into approval,
    not inferred from the atlas or framebuffer size. Live native geometry/desktop
    and retained ownership are independently rechecked by CalibrationWindowView.
    """
    def __init__(self, project, run=None, scope=None):
        from .mvp_policy_ingress import _existing_root, PolicyIngress
        self.project = _existing_root(project)
        self.run = run
        self.ingress = PolicyIngress(self.project)
        self._revoked = False
        self.scope = CalibrationScope.decode(self._read('calibration.json',8192))
        need(scope is None or scope == self.scope)
        self.spec = CalibrationSpec.decode(self._read('calibration-spec.json',262144),self.scope)
        self.data = json.loads(self.spec.raw)
        self.witness = self._read('calibration-host.json',8192)
        self._host_witness()
        self.slot = self._slot()
        self.expected_title = self.slot.display_name
        from .mvp_policy_ingress import _require_private
        _require_private(self.project / self.scope.private_root)
        if run is not None:
            self.scope.require_run(run)
            need(self.approval_current(run))

    def _read(self,name,maximum):
        from .mvp_policy_ingress import _require_private
        from .mvp_deployment_profile import read_private
        relative = 'private/deployment/'+name
        try:
            _require_private(self.project / relative)
            raw = read_private(self.project,relative,maximum)
            _require_private(self.project / relative)
            return raw
        except BaseException:
            raise RuntimeError('CALIBRATION_PRIVATE_SOURCE_UNAVAILABLE') from None

    def _host_witness(self):
        d = json.loads(self.witness)
        need(canonical_v3(d) == self.witness and type(d) is dict and set(d) == {
            'schema','host_profile_digest','configuration_digest','source_tree',
            'native_size','windows_dpi','observed_at','expires_at'})
        need(type(d['schema']) is int and d['schema'] == 1
            and digest_v3(self.witness) == self.data['host_witness_digest']
            and d['host_profile_digest'] == HOST_PROFILE
            and d['configuration_digest'] == self.data['configuration_digest']
            and d['source_tree'] == self.scope.candidate_tree
            and type(d['native_size']) is list and all(type(v) is int for v in d['native_size'])
            and tuple(d['native_size']) == self.spec.size
            and type(d['windows_dpi']) is int and d['windows_dpi'] == 96
            and type(d['observed_at']) is int and type(d['expires_at']) is int
            and 0 <= d['observed_at'] <= time.time() < d['expires_at']
            and 0 < d['expires_at']-d['observed_at'] <= 300)

    def _slot(self):
        from .mvp_policy_ingress import _require_private
        from .mvp_deployment_profile import _read_windows
        from .mvp_installed_build import CONFIG
        from .registry import load_instance_slots
        _require_private(self.project / 'private/slots.json')
        slots = _read_windows(self.project,('private','slots.json'),8192)
        raw = CONFIG.read_bytes()
        need(digest_v3(slots) == self.data['slots_digest']
            and digest_v3(raw) == self.data['configuration_digest'])
        slot = load_instance_slots(raw.decode('utf-8'),json.loads(slots))[self.scope.slot]
        need(slot.slot_id == self.data['slot_id']
            and digest_v3(slot.internal_name.encode()) == self.scope.instance_digest)
        return slot

    def approval_current(self,run):
        try:
            need(not self._revoked and run == self.run)
            self.scope.require_run(run)
            need(run.started_ns <= time.monotonic_ns() < run.deadline_ns)
            raw,payload = self.ingress._approval('CALIBRATION_READ_ONLY')
            request = asdict(run); del request['approval_digest']
            need(raw['run'] == request and digest_v3(payload) == run.approval_digest
                and self._read('calibration.json',8192) == canonical_v3(asdict(self.scope))
                and self._read('calibration-spec.json',262144) == self.spec.raw
                and self._read('calibration-host.json',8192) == self.witness
                and self._slot() == self.slot)
            self._host_witness()
            need(self.ingress._approval('CALIBRATION_READ_ONLY')[1] == payload
                and time.monotonic_ns() < run.deadline_ns)
            return True
        except BaseException:
            self._revoked = True
            return False

    def register_worker(self, session):
        from .mvp_worker_ownership import publish_worker
        need(session.run == self.run and session.calibration_scope == self.scope)
        publish_worker(self.project, session)

    def retirement_evidence(self, observation, offer, binding=None):
        """Independent protected read after Jw retirement; no writer can survive."""
        from .mvp_policy_ingress import _require_private, _read_input_state
        from .mvp_deployment_profile import read_private
        need(self.approval_current(self.run) is True and observation.status == 'CAPTURED'
            and observation.scope_digest == self.scope.digest
            and observation.offer_digest == digest_v3(canonical_v3(asdict(offer))))
        relative = self.scope.private_root + '/' + self.scope.run_nonce + '.json'
        _require_private(self.project / relative)
        raw = read_private(self.project, relative, 524288)
        _require_private(self.project / relative)
        need(digest_v3(raw) == observation.evidence_digest)
        try:
            d = json.loads(raw)
            need(canonical_v3(d) == raw and set(d) == {'schema','scope_digest',
                'specification_digest','offer_digest','window','observed_ns','crops'}
                and type(d['schema']) is int and d['schema'] == 2
                and d['scope_digest'] == self.scope.digest
                and d['specification_digest'] == digest_v3(self.spec.raw)
                and d['offer_digest'] == observation.offer_digest
                and self.scope.issued_ns <= d['observed_ns'] <= observation.observed_ns
                and d['window']['player'] == asdict(offer.player)
                and d['window']['supported_visible'] is True
                and (d['window']['width'],d['window']['height']) == self.spec.size
                and set(d['crops']) == set(CROP_IDS))
            from .lifecycle import ProcessIdentity
            window_data = dict(d['window'])
            window_data['player'] = ProcessIdentity(**window_data['player'])
            window_data['render'] = ProcessIdentity(**window_data['render'])
            window = WindowObservation(**window_data).require_v2()
            self._reopened_window = window
            if binding is not None:
                from .lifecycle import PlayerBinding
                need(type(binding) is PlayerBinding and binding.identity == offer.player
                    and binding.render_identity == window.render
                    and (binding.root_hwnd, binding.render_hwnd, binding.width, binding.height)
                    == (d['window']['root_hwnd'], d['window']['render_hwnd'],
                        d['window']['width'], d['window']['height']))
            for name,(x,y,r,b),expected in self.spec.crops:
                crop = bytes.fromhex(d['crops'][name])
                need(len(crop) == (r-x)*(b-y)*3 and digest_v3(crop) == expected)
        except (ValueError,KeyError,TypeError):
            raise RuntimeError('CALIBRATION_EVIDENCE_UNREADABLE') from None
        need(_read_input_state() == (0,)*256 and self.approval_current(self.run) is True)
        return digest_v3(raw)

    def captured_window(self, observation, offer):
        self.retirement_evidence(observation, offer)
        return self._reopened_window

    def worker_sources(self,channel,host):
        context = channel.context
        need(context.calibration is not None and context.calibration.scope == self.scope
            and context.run == self.run)
        current = lambda: channel.no_more_gameplay is False and self.approval_current(self.run)
        view = CalibrationWindowView(host=host,offer=context.offer,scope=self.scope,
            current=current,native_size=self.spec.size)
        observer = NativeCalibrationObserver(self.spec,view,
            NarrowEvidenceSink(self.project,self.spec),current=current,
            expected_title=self.expected_title)
        # Context/authority validation precedes ACK and PlayerReady. The view's
        # separate readiness gate and observe's WorkerLease guard all capture.
        return CalibrationSources(self.scope,current=lambda:self.approval_current(self.run),observer=observer)


class NativeCalibrationObserver:
    def __init__(self, spec, view, sink, *, current, clock_ns=time.monotonic_ns, wait=time.sleep,
                 expected_title=None):
        need(type(spec) is CalibrationSpec and callable(current))
        self.expected_title = expected_title
        self.spec, self.view, self.sink = spec,view,sink
        self.current,self.clock,self.wait = current,clock_ns,wait
        self._used = False
        self._last = 0

    def _check(self):
        need(self.current() is True)
        if self.view.binding is None:
            # Only initial window discovery waits. Never retry capture, evidence
            # writes, or an established binding. Each failed view stays revoked.
            until = min(self.clock()+10_000_000_000,self.view.scope.expires_ns)
            for attempt in range(100):
                need(self.current() is True and self.clock() < until)
                try:
                    self.view.bind(self.expected_title)
                    break
                except RuntimeError:
                    need(type(self.view) is CalibrationWindowView and self.view.binding is None)
                    old = self.view
                    self.wait(.1)
                    self.view = CalibrationWindowView(host=old.host,offer=old.offer,scope=old.scope,
                        current=old._current,native_size=old._size,clock_ns=self.clock)
            need(self.view.binding is not None)
        now = self.clock()
        need(type(now) is int and now >= self._last)
        self._last = now
        self.view._bound()
        need(self.current() is True)
        after = self.clock()
        need(type(after) is int and after >= now)
        self._last = after
        return after

    def _sample(self):
        import numpy as np
        frame = None
        crops = None
        failed = False
        try:
            start = self._check()
            frame = self.view.capture_bgr()
            need(type(frame) is np.ndarray and frame.dtype == np.uint8
                and frame.shape == (self.spec.size[1],self.spec.size[0],3))
            crops = {name: frame[y:b,x:r].tobytes()
                     for name,(x,y,r,b),_ in self.spec.crops}
            end = self._check()
            need(0 <= end-start <= 1_000_000_000)
            supported = all(digest_v3(crops[name]) == expected for name,_,expected in self.spec.crops)
        except BaseException as exc:
            failed = True
            traceback.clear_frames(exc.__traceback__)
            crops = None
        finally:
            if type(frame) is np.ndarray: frame.fill(0)
            frame = None
        if failed: raise RuntimeError('CALIBRATION_SAMPLE_DENIED')
        return crops,supported,end

    def window_geometry(self, offer):
        need(offer == self.view.offer)
        _,supported,_ = self._sample()
        if not supported and not self._used:
            until = min(self.clock()+30_000_000_000,self.view.scope.expires_ns-1_000_000_000)
            for _ in range(300):
                if self.clock() >= until: break
                self.wait(.1)
                _,supported,_ = self._sample()
                if supported: break
        b = self.view.binding
        return WindowObservation(b.identity,b.root_hwnd,b.render_hwnd,b.width,b.height,supported,
                                 b.render_identity,b.capture_nonce)

    def capture_private_evidence(self, scope, offer, window):
        need(not self._used and offer == self.view.offer)
        self._used = True
        need(CalibrationSpec.decode(self.spec.raw,scope) == self.spec)
        first,ok,at = self._sample()
        need(ok)
        # Windows sleep/clock granularity is not an elapsed-time witness.
        # Poll the monotonic clock; never accept a short sleep as stable spacing.
        for _ in range(20):
            now = self._check()
            if now-at >= 100_000_000: break
            self.wait(.01)
        need(self._check()-at >= 100_000_000)
        second,ok,after = self._sample()
        need(ok and first == second and 100_000_000 <= after-at <= 1_000_000_000)
        self._check()
        need(after <= self._last <= after+1_000_000_000)
        result = self.sink.persist(scope,offer,window,second,after)
        self._check()
        need(self._last-after <= 1_000_000_000)
        return result
