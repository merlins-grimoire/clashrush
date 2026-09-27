"""Parent-owned bounded attack worker, with metadata-only held-mouse lease.

Reuses the repository's suspended/Job-owned child primitive. No game frame,
private identity, credential or key history crosses the shared-memory boundary.
An abnormal child is never retried and never receives a manufactured READY.
"""
from __future__ import annotations
import argparse
import ctypes
from multiprocessing import shared_memory
import os
from pathlib import Path
import secrets
import struct
import sys
import time

from .mvp_deployment import DeploymentError, finite

_MAGIC=b'CRDEP001'; _HEADER=struct.Struct('<8sIId')
_HELD=32;_REVOKED=33;_SIZE=64
OWNER_TIMEOUT=270  # remaining parent cleanup is a separate <=30s reserve
STOP_RESERVE=30.0
_context=None


class MouseLease:
    """Single parent/child lease; not an equal-privilege security sandbox."""
    def __init__(self,segment,*,owned):self.segment=segment;self.owned=owned;self.closed=False
    @classmethod
    def create(cls,deadline):
        finite(deadline)
        if deadline<=time.monotonic():raise DeploymentError('DEADLINE')
        segment=shared_memory.SharedMemory(create=True,size=_SIZE)
        obj=cls(segment,owned=True);segment.buf[:]=bytes(_SIZE)
        _HEADER.pack_into(segment.buf,0,_MAGIC,os.getpid(),secrets.randbelow(2**31-1)+1,deadline)
        return obj
    @classmethod
    def open(cls,name,*,expected_parent):
        if type(name) is not str or not name or len(name)>128:raise DeploymentError('OWNER_LEASE_INVALID')
        try:
            try:segment=shared_memory.SharedMemory(name=name,create=False,track=False)
            except TypeError:segment=shared_memory.SharedMemory(name=name,create=False)
            obj=cls(segment,owned=False)
            # Windows rounds named shared-memory mappings up to the system page
            # size, so ``segment.size`` can exceed the requested metadata size.
            # Reject truncation, but accept the platform-owned padding.
            if segment.size<_SIZE or obj.parent!=expected_parent or not obj.active():
                obj.close();raise DeploymentError('OWNER_LEASE_INVALID')
            return obj
        except DeploymentError:raise
        except BaseException:raise DeploymentError('OWNER_LEASE_UNAVAILABLE') from None
    def _header(self):
        if self.closed:raise DeploymentError('OWNER_LEASE_CLOSED')
        magic,parent,marker,deadline=_HEADER.unpack_from(self.segment.buf)
        if magic!=_MAGIC or parent<1 or not 1<=marker<2**32:raise DeploymentError('OWNER_LEASE_INVALID')
        finite(deadline)
        return parent,marker,deadline
    @property
    def parent(self):return self._header()[0]
    @property
    def marker(self):return self._header()[1]
    @property
    def deadline(self):return self._header()[2]
    @property
    def name(self):return self.segment.name
    @property
    def held(self):
        self._header()
        value=self.segment.buf[_HELD]
        if value not in (0,1):raise DeploymentError('OWNER_LEASE_INVALID')
        return value==1
    def active(self):
        try:return self.segment.buf[_REVOKED]==0 and time.monotonic()<self.deadline
        except BaseException:return False
    def set_held(self,value: bool):
        if type(value) is not bool:raise DeploymentError('OWNER_LEASE_INVALID')
        self._header()
        if value and not self.active():raise DeploymentError('AUTHORIZATION_LOST')
        self.segment.buf[_HELD]=int(value)
    def revoke(self):
        self._header();self.segment.buf[_REVOKED]=1
    def close(self):
        if self.closed:return
        if self.owned:
            try:self.segment.unlink()
            except FileNotFoundError:pass
        self.segment.close();self.closed=True
    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def retire_mouse(lease: MouseLease,up) -> bool:
    """Independently attempt paired release, never a new press or click."""
    try:
        lease.revoke()
        if lease.held:
            up(lease.marker)
            lease.set_held(False)
        return True
    except BaseException:return False


def _native_up(marker):
    if os.name!='nt':raise DeploymentError('WINDOWS_REQUIRED')
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.mouse_event.argtypes=[ctypes.c_uint32]*4+[ctypes.c_void_p]
    user.mouse_event.restype=None
    user.mouse_event(0x0004,0,0,0,ctypes.c_void_p(marker))


def require_worker_context() -> MouseLease:
    if type(_context) is not MouseLease or _context.parent!=os.getppid() or not _context.active():
        raise DeploymentError('SUPERVISED_WORKER_REQUIRED')
    return _context


def run_supervised_attack(project_root: str,slots_path: str,transaction_ref: str):
    """The installed CLI calls this parent, not the old direct attack entry.

    Imports the existing native child owner lazily. No launch is possible without
    the child's original durable admission; no timeout/retry changes its state.
    """
    if os.name!='nt':raise DeploymentError('WINDOWS_REQUIRED')
    from .diagnostic_child_job import run_owned_diagnostic_child,DiagnosticChildOutcome
    from .mvp_local_gameplay import VisitResult
    from .mvp_session_authority import DurableSessionAuthority
    from .mvp_deployment_receipt import autonomy_status
    project=Path(project_root).resolve(strict=True)
    # Validate profile/grant before even spawning a worker (child revalidates).
    from .mvp_local_native import _candidate_tree
    from .mvp_deployment_profile import load_profile
    from .mvp_deployment_receipt import require_profile
    authority=DurableSessionAuthority(project/'var'/'mvp-session.sqlite3')
    try:
        status=authority.status()
        profile=load_profile(project,candidate_tree=_candidate_tree(project))
        require_profile(authority,status.run_nonce,profile.digest)
    finally:authority.close()
    with MouseLease.create(time.monotonic()+OWNER_TIMEOUT) as lease:
        command=[sys.executable,'-m','clash_rush_rebuild.mvp_deployment_worker',
                 '--project-root',str(project),'--slots',slots_path,
                 '--transaction-ref',transaction_ref,'--lease',lease.name]
        outcome=None;mouse_released=False
        try:outcome=run_owned_diagnostic_child(command,timeout=OWNER_TIMEOUT)
        finally:mouse_released=retire_mouse(lease,_native_up)
        if (not mouse_released or type(outcome) is not DiagnosticChildOutcome
                or outcome.cleanup_succeeded is not True or outcome.child_wait_completed is not True
                or outcome.operational_failure is not False or outcome.returncode!=0
                or outcome.stdout!='AUTONOMOUS\n' or outcome.stderr!=''):
            raise DeploymentError('SUPERVISED_ATTACK_INCOMPLETE')
    authority=DurableSessionAuthority(project/'var'/'mvp-session.sqlite3')
    try:
        if autonomy_status(authority,transaction_ref)!='AUTONOMOUS':raise DeploymentError('AUTONOMY_UNPROVED')
    finally:authority.close()
    return VisitResult('completed','AUTONOMOUS_RETURNED_HOME',True,True)


def main(argv=None):
    global _context
    parser=argparse.ArgumentParser(description='Private bounded deployment worker')
    for flag in ('project-root','slots','transaction-ref','lease'):parser.add_argument('--'+flag,required=True)
    lease=None;complete=False
    try:
        args=parser.parse_args(argv)
        if os.name!='nt':raise DeploymentError('WINDOWS_REQUIRED')
        lease=MouseLease.open(args.lease,expected_parent=os.getppid());_context=lease
        from .mvp_local_native import run_native_mvp_visit
        from .mvp_session_authority import DurableSessionAuthority
        from .mvp_deployment_receipt import autonomy_status
        result=run_native_mvp_visit(args.project_root,args.slots,args.transaction_ref)
        a=DurableSessionAuthority(Path(args.project_root)/'var'/'mvp-session.sqlite3')
        try:complete=getattr(result,'confirmed',None) is True and autonomy_status(a,args.transaction_ref)=='AUTONOMOUS'
        finally:a.close()
    except BaseException:
        complete=False
    finally:
        _context=None
        if lease is not None:
            try:lease.close()
            except BaseException:complete=False
    if complete:
        print('AUTONOMOUS');return 0
    sys.stderr.write('DEPLOYMENT_INCOMPLETE\n');return 2


if __name__=='__main__':raise SystemExit(main())
