"""Deployment-only Win32 delivery and intervention observation.

No native libraries are opened on import. This composes the existing exact
Win32BoundInput ownership/authorization checks; it does not discover windows.
All generated mouse events are tagged. Hook callbacks never suppress events and
retain only a sticky intervention bit, never keys, text, positions or images.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time

from .mvp_deployment import Action, DeploymentError, Intent, Intervention, Point, finite


class GuardedDeploymentInput:
    def __init__(self, *, mouse, authorize, monitor, lease, monotonic, wait, observed_events):
        self.mouse=mouse; self.authorize=authorize; self.monitor=monitor
        self.lease=lease; self.clock=monotonic; self.wait=wait
        self.held=False; self.last=-1.0
        self.observed_events=observed_events
    def _guard(self,action,deadline):
        try:
            if self.authorize(action) is not True or self.monitor() is not Intervention.CLEAR:
                return False
            now=finite(self.clock())
            if now<self.last: return False
            self.last=now
            return now<deadline
        except BaseException: return False
    def _counter(self):
        n=self.observed_events()
        if type(n) is not int or not 0<=n<2**63:raise DeploymentError('INTERVENTION_MONITOR_UNAVAILABLE')
        return n
    def _event(self,operation,deadline=None,*,cleanup=False):
        before=None
        try:before=self._counter()
        except BaseException:
            if not cleanup:return False
        # Cleanup still attempts up when the monitor or clock has failed.
        result=operation()
        if result is False or before is None:return False
        end=finite(self.clock())+.12
        if deadline is not None:end=min(end,deadline)
        for _ in range(16):
            count=self._counter()
            if count<before:return False
            if count>before:return True
            now=finite(self.clock())
            if now>=end:return False
            self.wait(min(.01,end-now))
        return False
    def release(self):
        if not self.held: return True
        try:
            if not self._event(self.mouse.up,cleanup=True):return False
            self.held=False
            self.lease.set_held(False)
            return True
        except BaseException: return False
    def deliver(self,intent,proof):
        if type(intent) is not Intent or not callable(proof) or self.held: return False
        expected=(Action.ATTACK_NAVIGATION if intent.verb in ('attack','find_match','army_attack')
                  else Action.RETURN_HOME if intent.verb in ('end_battle','confirm_end','return_home')
                  else Action.TROOP_DEPLOYMENT)
        if intent.action is not expected or not self._guard(expected,intent.deadline): return False
        if intent.verb.startswith('scroll_'):
            if (intent.destination is None or abs(intent.point.y-intent.destination.y)>.015
                    or not .80<=intent.point.y<=.98 or not .80<=intent.destination.y<=.98): return False
        elif intent.destination is not None: return False
        if intent.verb!='hold' and not intent.verb.startswith('scroll_') and intent.duration!=0: return False
        ok=False
        try:
            if not self.mouse.prepare(intent.point): return False
            if not self._guard(expected,intent.deadline): return False
            if not self._event(lambda:self.mouse.move(intent.point),intent.deadline): return False
            self.wait(min(.04,max(0.0,intent.deadline-finite(self.clock()))))
            if proof() is not True or not self._guard(expected,intent.deadline): return False
            if self.mouse.at_target(intent.point) is not True: return False
            # Write possible-held intent BEFORE the OS mutation. A killed worker's
            # parent releases only this explicitly tracked button after retirement.
            self.lease.set_held(True); self.held=True
            start=finite(self.clock())
            if not self._event(self.mouse.down,intent.deadline):return False
            end=min(intent.deadline,start+(intent.duration or .02))
            while finite(self.clock())<end:
                if not self._guard(expected,intent.deadline): return False
                if intent.destination is not None:
                    fraction=min(1.0,(finite(self.clock())-start)/max(intent.duration,.001))
                    p=Point(intent.point.x+(intent.destination.x-intent.point.x)*fraction,
                            intent.point.y+(intent.destination.y-intent.point.y)*fraction)
                    if not self._event(lambda:self.mouse.move(p),intent.deadline): return False
                self.wait(min(.02,max(0.0,end-finite(self.clock()))))
            if intent.destination is not None:
                if not self._guard(expected,intent.deadline) or not self._event(lambda:self.mouse.move(intent.destination),intent.deadline): return False
            ok=self._guard(expected,intent.deadline)
        except BaseException:
            ok=False
        finally:
            if not self.release(): ok=False
        return ok


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
        if self.bound._foreground() is not True: return False
        self.bound._require_binding(b)
        return True
    def _pixel(self,point):
        b=self.bound._binding
        p=self.w.POINT(round(point.x*(b.width-1)),round(point.y*(b.height-1)))
        if not self.user.ClientToScreen(self.w.HWND(b.render_hwnd),ctypes.byref(p)):
            raise DeploymentError('COORDINATE_UNPROVED')
        return p.x,p.y
    def move(self,point):
        x,y=self._pixel(point)
        vx,vy,vw,vh=(self.user.GetSystemMetrics(n) for n in (76,77,78,79))
        if vw<=1 or vh<=1 or not vx<=x<vx+vw or not vy<=y<vy+vh: return False
        ax=round((x-vx)*65535/(vw-1)); ay=round((y-vy)*65535/(vh-1))
        self.user.mouse_event(0x0001|0x8000|0x4000,ax,ay,0,ctypes.c_void_p(self.marker))
        self.pixel=(x,y)
        return True
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
    def down(self):
        b=self.bound._binding
        self.bound._require_binding(b)
        self.user.mouse_event(0x0002,0,0,0,ctypes.c_void_p(self.marker))
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
        self.marker=marker; self.foreign=False; self.failed=False; self.owned_events=0
        self.beat=0.0; self.thread_id=None; self.ready=threading.Event(); self.done=threading.Event()
        self.thread=threading.Thread(target=self._loop,name='deployment-input-observer',daemon=True)
    def events(self):
        return self.owned_events
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
                        if int(event.extra)!=self.marker or not event.flags&1: self.foreign=True
                        else:self.owned_events+=1
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
