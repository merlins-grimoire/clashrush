"""Handle-backed discovery joined to the existing parent/run authority.

No interpreter, Hermes, image-name or parent-PID allowlist. Unreadable metadata
and unjoined runner-like processes are ambiguous, never absence evidence.
"""
from contextlib import contextmanager
from dataclasses import asdict
import re
from .lifecycle import ProcessIdentity
from .mvp_installed_build import ProcessObservation


@contextmanager
def observe_process(pid):
    # Reuse only the reviewed read-only Win32 metadata leaves, never transport.
    from .mvp_installed_build import _NativeLeaves
    leaves = object.__new__(_NativeLeaves)
    handle = leaves.open_player(pid)
    try:
        first = leaves.observe(handle)
        if first.identity.pid != pid or leaves.observe(handle) != first:
            raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
        yield first
        if leaves.observe(handle) != first:
            raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
    finally:
        leaves.close_player(handle)

def publish_worker(project, session):
    """Publish the suspended owned worker before first resume; never renew a lease."""
    from .mvp_calibration_native import write_protected_record
    from .mvp_session_authority import canonical_v3
    session.topology._parent_only()
    offer=session.topology.offer
    with observe_process(offer.worker.pid) as process:
        if process.identity != offer.worker:
            raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
        record=dict(schema=1,project=str(project),source_tree=session.run.source_tree,
            run_nonce=session.run.run_nonce,generation=session.run.generation,
            binding_digest=session.run.digest,parent=asdict(offer.parent),worker=asdict(offer.worker),
            executable=process.image,argv=list(process.argv),state='ACTIVE')
        if classify(process,record=record,current=None) != 'CONFLICT':
            raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
        payload=canonical_v3(record)
        if write_protected_record(project,'private/deployment/'+session.run.run_nonce+'.json',payload)!=payload:
            raise RuntimeError('POLICY_WORKER_RECORD_READBACK')
        # Applicable current-run caller: validate this retained Jw, not the
        # stopped-only host ingress. Helpers need no invented argv ownership.
        members=session.topology._host.stable_job_members(session.topology.worker_job)
        try:
            require_worker_absence((offer.parent.pid, *(i.pid for i in members.identities)),
                self_identity=offer.parent, observe=observe_process, load_record=load_record,
                current=record, owned_topology=session.topology)
        finally:
            members.close()


def load_record(process):
    """Resolve only an exact declared project/nonce; join the protected authority."""
    import json

    from .mvp_policy_ingress import _existing_root, _require_private
    from .mvp_deployment_profile import read_private
    from .mvp_session_authority import DurableSessionAuthority, canonical_v3
    if not runner_association(process):
        return None
    try:
        if process.argv.count('--project-root')!=1 or process.argv.count('--ownership-nonce')!=1:
            raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
        project=_existing_root(process.argv[process.argv.index('--project-root')+1])
        nonce=process.argv[process.argv.index('--ownership-nonce')+1]
        if re.fullmatch('[0-9a-f]{32}',nonce) is None:
            raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
        relative='private/deployment/'+nonce+'.json'
        _require_private(project/relative)
        payload=read_private(project,relative,16384)
        _require_private(project/relative)
        record=json.loads(payload)
        if canonical_v3(record)!=payload or classify(process,record=record,current=None)!='CONFLICT':
            raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
        path=project/'var/mvp-session.sqlite3'
        _require_private(path)
        authority=DurableSessionAuthority(path,read_only=True)
        try:
            run=authority.read_run_v3(record['generation'])
            status=authority.status_v3()
            closure=authority._readback_v3('closure_v3',run.generation)
            if (run.run_nonce!=nonce or run.digest!=record['binding_digest']
                    or run.source_tree!=record['source_tree'] or status.current_generation!=run.generation
                    or closure is not None and closure['closed']!=0):
                raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
            with observe_process(record['parent']['pid']) as parent:
                if asdict(parent.identity)!=record['parent']:
                    raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
            if read_private(project,relative,16384)!=payload:
                raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
        finally:
            authority.close()
        return record
    except (OSError,ValueError,TypeError,KeyError,IndexError):
        raise RuntimeError('POLICY_WORKER_AMBIGUOUS') from None


def require_worker_absence(pids, *, self_identity, observe, load_record, current=None,
                           owned_topology=None):
    """Every classification comes from retained metadata, never snapshot names."""
    if len(set(pids)) != len(pids) or pids.count(self_identity.pid) != 1:
        raise RuntimeError('POLICY_PROCESS_SNAPSHOT')
    for pid in pids:
        with observe(pid) as process:
            if process.identity.pid != pid:
                raise RuntimeError('POLICY_WORKER_AMBIGUOUS')
            if process.identity == self_identity:
                continue
            if owned_topology is not None and classify(process, record=current,
                    current=current, owned_topology=owned_topology) == 'OWNED':
                continue
            result = classify(process, record=load_record(process), current=current)
            if result not in ('UNRELATED','OWNED'):
                raise RuntimeError('POLICY_WORKER_' + result)


def runner_association(observation):
    from pathlib import PureWindowsPath
    # Installation directory is not executed runner code: venv Python is shared.
    # Actual runner arguments still flag renamed executables and other worktrees.
    return (any(v in observation.argv for v in ('--ownership-nonce','--project-root','--parent-created'))
            or any('clash_rush' in v.casefold() or 'clash-rush' in v.casefold()
                   for v in (PureWindowsPath(observation.image).name, *observation.argv[1:])))


def classify(observation, *, record, current, owned_topology=None):
    if owned_topology is not None:
        from .mvp_process_topology import ParentProcessTopology
        try:
            if type(owned_topology) is not ParentProcessTopology or record != current:
                return 'AMBIGUOUS'
            owned_topology._parent_only()
            offer=owned_topology.offer
            worker=ProcessObservation(offer.worker,record['executable'],tuple(record['argv']))
            if (record['parent'] != asdict(offer.parent) or record['run_nonce'] != offer.run_nonce
                    or record['generation'] != offer.generation
                    or classify(worker,record=record,current=current) != 'OWNED'):
                return 'AMBIGUOUS'
            members=owned_topology._host.stable_job_members(owned_topology.worker_job)
            try:
                return 'OWNED' if observation.identity in members.identities else 'AMBIGUOUS'
            finally:
                members.close()
        except (RuntimeError,KeyError,TypeError,AttributeError):
            return 'AMBIGUOUS'

    if (type(observation) is not ProcessObservation
            or type(observation.identity) is not ProcessIdentity
            or type(observation.identity.creation_time_100ns) is not int
            or observation.identity.creation_time_100ns <= 0
            or not observation.image or not observation.argv):
        return 'AMBIGUOUS'
    if record is None:
        # Metadata/negative markers cannot attest the executed workload. Until
        # a positive unrelatedness source exists, preserve conservative denial.
        return 'AMBIGUOUS'
    try:
        required={'schema','project','source_tree','run_nonce','generation','binding_digest',
                  'parent','worker','executable','argv','state'}
        if (type(record) is not dict or set(record)!=required or type(record['schema']) is not int
                or record['schema']!=1 or record['state']!='ACTIVE'
                or type(record['generation']) is not int or record['generation']<1
                or not all(type(record[k]) is str and re.fullmatch('[0-9a-f]{'+str(n)+'}',record[k])
                           for k,n in [('source_tree',40),('run_nonce',32),('binding_digest',64)])
                or record['worker']!=asdict(observation.identity)
                or record['executable']!=observation.image or tuple(record['argv'])!=observation.argv
                or observation.argv[observation.argv.index('--project-root')+1]!=record['project']
                or int(observation.argv[observation.argv.index('--parent-pid')+1])!=record['parent']['pid']
                or int(observation.argv[observation.argv.index('--parent-created')+1])!=record['parent']['creation_time_100ns']):
            return 'AMBIGUOUS'
        return 'OWNED' if current == record else 'CONFLICT'
    except (ValueError,TypeError,KeyError,IndexError):
        return 'AMBIGUOUS'
