"""Deployment-only Win32 delivery and intervention observation.

No native libraries are opened on import. This composes the existing exact
Win32BoundInput ownership/authorization checks; it does not discover windows.
All generated mouse events are tagged. Hook callbacks never suppress events and
retain only a sticky intervention bit, never keys, text, positions or images.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import os
import re
import threading
import time

from .mvp_deployment import Action, DeploymentError, Intent, Intervention, Point, finite


class HeldState(StrEnum):
    NOT_HELD = 'NOT_HELD'
    DOWN_SUBMITTING = 'DOWN_SUBMITTING'
    MAY_BE_HELD = 'MAY_BE_HELD'
    OWNED_HELD = 'OWNED_HELD'
    RELEASE_SUBMITTING = 'RELEASE_SUBMITTING'
    RELEASE_OBSERVED = 'RELEASE_OBSERVED'
    RELEASE_UNCERTAIN = 'RELEASE_UNCERTAIN'
    OWNERSHIP_LOST = 'OWNERSHIP_LOST'


@dataclass(frozen=True, slots=True)
class InputOwner:
    run_nonce: str
    generation: int
    binding_digest: str
    desktop_digest: str

    def __post_init__(self):
        if (type(self.run_nonce) is not str or re.fullmatch('[0-9a-f]{32}', self.run_nonce) is None
                or type(self.generation) is not int or self.generation <= 0
                or any(type(s) is not str or re.fullmatch('[0-9a-f]{64}', s) is None
                       for s in (self.binding_digest, self.desktop_digest))):
            raise DeploymentError('INPUT_OWNER_INVALID')


@dataclass(frozen=True, slots=True)
class ObservedInput:
    sequence: int
    kind: str
    marker: int
    observed: float


@dataclass(frozen=True, slots=True)
class PhysicalReceipt:
    owner: InputOwner
    gesture: int
    event: int
    kind: str
    marker: int
    started: float
    completed: float | None
    observation: ObservedInput | None


class GuardedDeploymentInput:
    """One live mouse owner. No count, durable held bit or parent can release.

    Keyboard/startup routes must stay disabled until they use this same event
    boundary; this mouse sink never guesses a key-up or a recovery gesture.
    """
    def __init__(self, *, mouse, authorize, monitor, lease, monotonic, wait,
                 observed_events, owner=None, arm_event=None, ownership_live=None,
                 before_event=None, after_event=None):
        self.mouse=mouse; self.authorize=authorize; self.monitor=monitor
        self.lease=lease; self.clock=monotonic; self.wait=wait
        self.observed_events=observed_events; self.arm_event=arm_event
        self.owner=owner; self.state=HeldState.NOT_HELD
        self.ownership_live=ownership_live
        self.before_event=before_event; self.after_event=after_event
        self.last=-1.0; self.deadline=0.; self.action=None
        self.gesture=0; self.event=0; self.receipts=[]
        self.emergency_release_required=False; self.uncertain=False
        self._thread=threading.get_ident(); self._dispatching=False
        self._last_events=()
        self._pending_receipts=[]

    @property
    def held(self):
        return self.state not in (HeldState.NOT_HELD, HeldState.RELEASE_OBSERVED)

    def _now(self):
        now=finite(self.clock())
        if now<self.last: raise DeploymentError('INPUT_CLOCK_REGRESSED')
        self.last=now
        return now

    def _events(self):
        events=self.observed_events()
        if type(events) is not tuple or len(events)>100000:
            raise DeploymentError('INPUT_ATTRIBUTION_UNAVAILABLE')
        for i,event in enumerate(events):
            if (type(event) is not ObservedInput or type(event.sequence) is not int
                    or event.sequence!=i+1 or type(event.kind) is not str
                    or event.kind not in ('MOVE','DOWN','UP') or type(event.marker) is not int
                    or not 1<=event.marker<2**32 or type(event.observed) not in (int,float)
                    or finite(event.observed)<0
                    or (i and event.observed<events[i-1].observed)):
                raise DeploymentError('INPUT_ATTRIBUTION_UNAVAILABLE')
        if events[:len(self._last_events)]!=self._last_events:
            raise DeploymentError('INPUT_OBSERVATION_GAP')
        self._last_events=events
        return events

    def _guard(self,action,deadline):
        try:
            if (threading.get_ident()!=self._thread or type(self.owner) is not InputOwner
                    or not callable(self.arm_event) or self.uncertain or self.emergency_release_required
                    or not callable(self.ownership_live) or self.ownership_live() is not True
                    or self.authorize(action) is not True or self.monitor() is not Intervention.CLEAR):
                return False
            return self._now()<deadline
        except BaseException: return False

    def _event(self,kind,operation,*,emergency=False):
        before=self._events()
        self.event+=1
        # Exact run/gesture/event marker, not a reusable per-process tag.
        raw=f'{self.owner.run_nonce}:{self.owner.generation}:{self.gesture}:{self.event}'.encode('ascii')
        marker=int.from_bytes(hashlib.sha256(raw).digest()[:4],'little') or 1
        if any(r.marker==marker for r in self.receipts):
            raise DeploymentError('INPUT_MARKER_COLLISION')
        if self.arm_event(kind,marker) is not True: return False
        self.mouse.marker=marker
        if kind!='UP' and not emergency and self.before_event is not None:
            if self.before_event(self.owner,self.gesture,self.event,kind,marker,self._now()) is not True:
                return False
        if emergency:
            if (self.monitor() is not Intervention.CLEAR
                    or self.ownership_live() is not True): return False
        elif not self._guard(self.action,self.deadline):
            if kind!='UP':return False
            # The single claimed inverse may cross D during its last guard.
            # Reclassify, never replay; current ownership is checked below.
            emergency=True
            self.emergency_release_required=True;self.uncertain=True
        started=self._now()
        if not emergency and started>=self.deadline:
            if kind!='UP':return False
            emergency=True
            self.emergency_release_required=True;self.uncertain=True
        if emergency and (kind!='UP' or self.state is not HeldState.RELEASE_SUBMITTING
                or self.monitor() is not Intervention.CLEAR or self.ownership_live() is not True):
            return False
        observation=None; completed=None
        try:
            result=operation()
            if result is False: return False
            end=self._now()+.12
            for _ in range(16):
                events=self._events(); now=self._now()
                if self.monitor() is not Intervention.CLEAR: return False
                if len(events)>len(before):
                    if len(events)!=len(before)+1: return False
                    found=events[-1]
                    if (found.kind!=kind or found.marker!=marker
                            or not started<=found.observed<=now): return False
                    observation=found; completed=now
                    return True
                if now>=end: return False
                self.wait(min(.01,end-now))
            return False
        finally:
            receipt=PhysicalReceipt(self.owner,self.gesture,self.event,
                                    kind,marker,started,completed,observation)
            self.receipts.append(receipt)
            self._pending_receipts.append((receipt,emergency))

    def _publish_receipts(self):
        # Never invoke storage while this sink can still owe a live inverse.
        pending,self._pending_receipts=self._pending_receipts,[]
        try:
            if self.after_event is not None:
                for receipt,emergency in pending:
                    self.after_event(receipt,emergency)
        except BaseException:
            self.uncertain=True
            return False
        return True

    def release(self):
        if threading.get_ident()!=self._thread: return False
        if self.state in (HeldState.NOT_HELD,HeldState.RELEASE_OBSERVED):
            return not self.uncertain
        if self.state is not HeldState.OWNED_HELD: return False
        try:
            # Only the current live owner with uninterrupted ordered observation
            # may claim the inverse. Never foreground, move or capture here.
            events=self._events()
            if (self.monitor() is not Intervention.CLEAR or self.ownership_live() is not True
                    or not self.receipts
                    or self.receipts[-1].observation!=events[-1]):
                self.state=HeldState.OWNERSHIP_LOST; self.uncertain=True
                return False
            emergency=(self._now()>=self.deadline or self.authorize(self.action) is not True)
            if emergency:
                self.emergency_release_required=True; self.uncertain=True
            self.state=HeldState.RELEASE_SUBMITTING
            if not self._event('UP',self.mouse.up,emergency=emergency):
                self.state=HeldState.RELEASE_UNCERTAIN; self.uncertain=True
                return False
            self.state=HeldState.RELEASE_OBSERVED
            self.lease.set_held(False)  # metadata only, never input authority
            if self.receipts[-1].completed>=self.deadline:
                self.emergency_release_required=True; self.uncertain=True
            return True
        except BaseException:
            self.state=HeldState.RELEASE_UNCERTAIN; self.uncertain=True
            return False

    def deliver(self,intent,proof):
        if (type(intent) is not Intent or not callable(proof) or self.held
                or self._dispatching or self.uncertain or self.emergency_release_required): return False
        try:
            intent=Intent(intent.verb,Point(intent.point.x,intent.point.y),intent.action,
                          intent.card_id,intent.duration,intent.destination,intent.deadline)
        except BaseException: return False
        expected=(Action.ATTACK_NAVIGATION if intent.verb in ('attack','find_match','army_attack')
                  else Action.RETURN_HOME if intent.verb=='return_home'
                  else Action.TROOP_DEPLOYMENT if intent.verb in ('select','hold','tap') else None)
        if expected is None or intent.action is not expected or intent.destination is not None: return False
        if intent.verb!='hold' and intent.duration!=0: return False
        if not self._guard(expected,intent.deadline): return False
        self.action=expected; self.deadline=intent.deadline
        self.gesture+=1; self.event=0; self.state=HeldState.NOT_HELD
        self._dispatching=True; ok=False
        try:
            # Validate attribution before any physical route, including prepare.
            self._events()
            if not self._guard(expected,intent.deadline): return False
            if self.mouse.prepare(intent.point) is not True: return False
            prepare_move=getattr(type(self.mouse),'prepare_move',None)
            commit_move=getattr(type(self.mouse),'commit_move',None)
            prepared_move=prepare_move(self.mouse,intent.point) if prepare_move is not None else None
            move=(lambda:commit_move(self.mouse,prepared_move)) if commit_move is not None else lambda:self.mouse.move(intent.point)
            if not self._event('MOVE',move): return False
            if self.receipts[-1].completed>=self.deadline:
                self.uncertain=True; return False
            self.wait(min(.04,max(0.0,self.deadline-self._now())))
            if proof() is not True or not self._guard(expected,self.deadline): return False
            if self.mouse.at_target(intent.point) is not True: return False
            prepare=getattr(type(self.mouse),'prepare_down',None)
            commit=getattr(type(self.mouse),'commit_down',None)
            prepared=prepare(self.mouse,intent.point) if prepare is not None else None
            self.lease.set_held(True)
            if not self._guard(expected,self.deadline):
                self.lease.set_held(False); return False
            self.state=HeldState.DOWN_SUBMITTING
            operation=(lambda:commit(self.mouse,prepared)) if commit is not None else self.mouse.down
            if not self._event('DOWN',operation):
                self.state=HeldState.MAY_BE_HELD; self.uncertain=True; return False
            self.state=HeldState.OWNED_HELD
            if self.receipts[-1].completed>=self.deadline: return False
            end=min(self.deadline,self.receipts[-1].started+(intent.duration or .02))
            while self._now()<end:
                if not self._guard(expected,self.deadline): return False
                self.wait(min(.02,max(0.,end-self._now())))
            ok=self._guard(expected,self.deadline)
        except BaseException:
            if self.state is HeldState.DOWN_SUBMITTING:
                self.state=HeldState.MAY_BE_HELD; self.uncertain=True
            ok=False
        finally:
            if not self.release(): ok=False
            if not self._publish_receipts(): ok=False
            self._dispatching=False
        return ok and not self.uncertain and not self.emergency_release_required


class TaggedWin32Mouse:
    """Uses the existing binding owner; no HWND/PID discovery or new keymap."""
    def __init__(self,bound_input,marker: int):
        if os.name!='nt': raise DeploymentError('WINDOWS_REQUIRED')
        if type(marker) is not int or not 1<=marker<2**32: raise DeploymentError('INPUT_MARKER_INVALID')
        from ctypes import wintypes as w
        self.bound=bound_input; self.marker=marker; self.w=w
        self.user=bound_input._user32
        self.user.GetSystemMetrics.argtypes=[ctypes.c_int]
        self.user.GetSystemMetrics.restype=ctypes.c_int
        self.pixel=None
    def prepare(self,point):
        b=self.bound._binding
        self.bound._require_binding(b)
        # No focus repair: preparation is read-only and may cross D. The sink
        # rechecks D after it, immediately before the raw input leaf.
        active=self.user.GetForegroundWindow()
        if not active or int(self.user.GetAncestor(active,2))!=b.root_hwnd: return False
        self.bound._require_binding(b)
        return True
    def _pixel(self,point):
        b=self.bound._binding
        p=self.w.POINT(round(point.x*(b.width-1)),round(point.y*(b.height-1)))
        if not self.user.ClientToScreen(self.w.HWND(b.render_hwnd),ctypes.byref(p)):
            raise DeploymentError('COORDINATE_UNPROVED')
        return p.x,p.y
    def prepare_move(self,point):
        x,y=self._pixel(point)
        vx,vy,vw,vh=(self.user.GetSystemMetrics(n) for n in (76,77,78,79))
        if vw<=1 or vh<=1 or not vx<=x<vx+vw or not vy<=y<vy+vh:
            raise DeploymentError('COORDINATE_UNPROVED')
        ax=round((x-vx)*65535/(vw-1)); ay=round((y-vy)*65535/(vh-1))
        return x,y,ax,ay
    def commit_move(self,prepared):
        x,y,ax,ay=prepared
        self.user.mouse_event(0x0001|0x8000|0x4000,ax,ay,0,ctypes.c_void_p(self.marker))
        self.pixel=(x,y)
        return True
    def move(self,point):
        return self.commit_move(self.prepare_move(point))
    def at_target(self,point):
        b=self.bound._binding
        self.bound._require_binding(b)
        active=self.user.GetForegroundWindow()
        if not active or int(self.user.GetAncestor(active,2))!=b.root_hwnd: return False
        current=self._pixel(point)
        cursor=self.w.POINT()
        if not self.user.GetCursorPos(ctypes.byref(cursor)): return False
        self.bound._require_binding(b)
        return self.pixel==current==(cursor.x,cursor.y)
    def prepare_down(self,_point):
        b=self.bound._binding
        self.bound._require_binding(b)
        return None
    def commit_down(self,_prepared):
        self.user.mouse_event(0x0002,0,0,0,ctypes.c_void_p(self.marker))
    def down(self):
        self.commit_down(self.prepare_down(None))
    def up(self): self.user.mouse_event(0x0004,0,0,0,ctypes.c_void_p(self.marker))


class WindowsInterventionMonitor:
    """Conservative session-local hook observer; lost health means UNKNOWN.

    This is not an anti-tamper/security boundary against another equal-privilege
    process or a compromised OS. It is attribution evidence for normal operator
    intervention. Every foreign mouse/key event is conservatively intervention.
    """
    def __init__(self,marker: int):
        if os.name!='nt': raise DeploymentError('WINDOWS_REQUIRED')
        if type(marker) is not int or not 1<=marker<2**32: raise DeploymentError('INPUT_MARKER_INVALID')
        self.marker=marker; self.foreign=False; self.failed=False
        self._events=[]; self._expected=None; self._markers=set(); self._lock=threading.Lock()
        self.beat=0.0; self.thread_id=None; self.ready=threading.Event(); self.done=threading.Event()
        self.thread=threading.Thread(target=self._loop,name='deployment-input-observer',daemon=True)
    def events(self):
        with self._lock: return tuple(self._events)
    def arm(self,kind,marker):
        if (type(kind) is not str or kind not in ('MOVE','DOWN','UP')
                or type(marker) is not int or not 1<=marker<2**32
                or self.state() is not Intervention.CLEAR): return False
        with self._lock:
            if self._expected is not None or marker in self._markers: return False
            self._expected=(kind,marker); self._markers.add(marker)
            return True
    def _observe_mouse(self,kind,marker,injected,observed):
        with self._lock:
            if (injected is not True or self._expected!=(kind,marker)
                    or len(self._events)>=100000):
                self.foreign=True
                return
            self._events.append(ObservedInput(len(self._events)+1,kind,marker,observed))
            self._expected=None
    def start(self):
        self.thread.start()
        if not self.ready.wait(1.0) or self.failed or self.state() is not Intervention.CLEAR:
            self.close(); raise DeploymentError('INTERVENTION_MONITOR_UNAVAILABLE')
    def state(self):
        if self.foreign: return Intervention.DETECTED
        if self.failed or self.done.is_set() or not self.thread.is_alive() or time.monotonic()-self.beat>.4:
            return Intervention.UNKNOWN
        return Intervention.CLEAR
    def close(self):
        if self.thread_id is not None and self.thread.is_alive():
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.PostThreadMessageW.argtypes=[ctypes.c_uint32,ctypes.c_uint32,ctypes.c_size_t,ctypes.c_ssize_t]
            user.PostThreadMessageW.restype=ctypes.c_int32
            if not user.PostThreadMessageW(self.thread_id,0x0012,0,0): self.failed=True
            self.thread.join(1.0)
        if self.thread.is_alive(): self.failed=True
        return not self.failed and not self.thread.is_alive()
    def _loop(self):
        user=kernel=None; hooks=[]; timer=0
        try:
            user=ctypes.WinDLL('user32',use_last_error=True)
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            PTR=ctypes.c_void_p; U32=ctypes.c_uint32; I32=ctypes.c_int32
            UP=ctypes.c_size_t; SP=ctypes.c_ssize_t
            class XY(ctypes.Structure): _fields_=[('x',I32),('y',I32)]
            class Mouse(ctypes.Structure):
                _fields_=[('pt',XY),('data',U32),('flags',U32),('time',U32),('extra',UP)]
            class Key(ctypes.Structure):
                _fields_=[('vk',U32),('scan',U32),('flags',U32),('time',U32),('extra',UP)]
            class MSG(ctypes.Structure):
                _fields_=[('hwnd',PTR),('message',U32),('wParam',UP),('lParam',SP),('time',U32),('pt',XY),('private',U32)]
            Hook=ctypes.WINFUNCTYPE(SP,I32,UP,SP)
            user.SetWindowsHookExW.argtypes=[I32,Hook,PTR,U32]; user.SetWindowsHookExW.restype=PTR
            user.CallNextHookEx.argtypes=[PTR,I32,UP,SP]; user.CallNextHookEx.restype=SP
            user.UnhookWindowsHookEx.argtypes=[PTR]; user.UnhookWindowsHookEx.restype=I32
            user.GetMessageW.argtypes=[ctypes.POINTER(MSG),PTR,U32,U32]; user.GetMessageW.restype=I32
            user.PeekMessageW.argtypes=[ctypes.POINTER(MSG),PTR,U32,U32,U32]; user.PeekMessageW.restype=I32
            user.SetTimer.argtypes=[PTR,UP,U32,PTR]; user.SetTimer.restype=UP
            user.KillTimer.argtypes=[PTR,UP]; user.KillTimer.restype=I32
            kernel.GetCurrentThreadId.restype=U32
            kernel.GetModuleHandleW.argtypes=[ctypes.c_wchar_p]; kernel.GetModuleHandleW.restype=PTR
            def mouse_hook(code,wp,lp):
                try:
                    if code>=0:
                        event=ctypes.cast(lp,ctypes.POINTER(Mouse)).contents
                        kind={0x0200:'MOVE',0x0201:'DOWN',0x0202:'UP'}.get(int(wp))
                        self._observe_mouse(kind,int(event.extra),bool(event.flags&1),time.monotonic())
                except BaseException: self.failed=True
                return user.CallNextHookEx(None,code,wp,lp)
            def key_hook(code,wp,lp):
                # This deployment implementation emits no keys. Any key is foreign.
                if code>=0: self.foreign=True
                return user.CallNextHookEx(None,code,wp,lp)
            mouse_cb=Hook(mouse_hook); key_cb=Hook(key_hook)
            message=MSG(); user.PeekMessageW(ctypes.byref(message),None,0,0,0)
            self.thread_id=kernel.GetCurrentThreadId()
            module=kernel.GetModuleHandleW(None)
            for kind,callback in ((14,mouse_cb),(13,key_cb)):
                h=user.SetWindowsHookExW(kind,callback,module,0)
                if not h: raise DeploymentError('INTERVENTION_MONITOR_UNAVAILABLE')
                hooks.append(h)
            timer=user.SetTimer(None,0,100,None)
            if not timer: raise DeploymentError('INTERVENTION_MONITOR_UNAVAILABLE')
            self.beat=time.monotonic(); self.ready.set()
            while True:
                status=user.GetMessageW(ctypes.byref(message),None,0,0)
                if status==0: break
                if status<0: raise DeploymentError('INTERVENTION_MONITOR_UNAVAILABLE')
                self.beat=time.monotonic()
        except BaseException:
            self.failed=True; self.ready.set()
        finally:
            if user is not None:
                if timer and not user.KillTimer(None,timer): self.failed=True
                for h in reversed(hooks):
                    if not user.UnhookWindowsHookEx(h): self.failed=True
            self.done.set()
