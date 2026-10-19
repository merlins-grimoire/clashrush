"""Installed closure rejects a correct label over altered/mixed source bytes."""
from hashlib import sha256
import json
from types import SimpleNamespace
import pytest
from clash_rush_rebuild.mvp_runner_manifest import runner_manifest,verify_runner_manifest,RunnerManifestError
from test_policy_parent_session import compose,prepare


def package(tmp_path):
    root=tmp_path/'package';root.mkdir()
    (root/'__init__.py').write_bytes(b'')
    (root/'worker.py').write_bytes(b'VALUE=1\n')
    (root/'policy.json').write_bytes(b'{"input":false}')
    return root


def check(root,payload,modules=None):
    return verify_runner_manifest(payload,approved_sha256=sha256(payload).hexdigest(),
        approved_tree='a'*40,package_root=root,loaded_modules={} if modules is None else modules)


def test_valid_export_and_loaded_package_have_exact_membership(tmp_path):
    root=package(tmp_path);payload=runner_manifest(root,'a'*40)
    modules={'clash_rush_rebuild':SimpleNamespace(__file__=str(root/'__init__.py'),__path__=[str(root)]),
             'clash_rush_rebuild.worker':SimpleNamespace(__file__=str(root/'worker.py'))}
    result=check(root,payload,modules)
    assert result.files==tuple((name,sha256((root/name).read_bytes()).hexdigest())
                               for name in ('__init__.py','policy.json','worker.py'))


@pytest.mark.parametrize('change',['module','asset','missing','extra','other_import','package_path','duplicate','traversal','schema_bool','tree'])
def test_same_tree_label_never_substitutes_for_installed_membership(tmp_path,change):
    root=package(tmp_path);payload=runner_manifest(root,'a'*40);modules={}
    if change=='module':(root/'worker.py').write_bytes(b'VALUE=2\n')
    if change=='asset':(root/'policy.json').write_bytes(b'{"input":true}')
    if change=='missing':(root/'worker.py').unlink()
    if change=='extra':(root/'override.py').write_bytes(b'')
    if change=='other_import':
        other=tmp_path/'outside.py';other.write_bytes(b'VALUE=1\n')
        modules={'clash_rush_rebuild.worker':SimpleNamespace(__file__=str(other))}
    if change=='package_path':
        modules={'clash_rush_rebuild':SimpleNamespace(__file__=str(root/'__init__.py'),__path__=[str(root),str(tmp_path)])}
    if change in ('duplicate','traversal','schema_bool','tree'):
        data=json.loads(payload)
        if change=='duplicate':data['files'].append(data['files'][0])
        if change=='traversal':data['files'][0][0]='../__init__.py'
        if change=='schema_bool':data['schema']=True
        if change=='tree':data['source_tree']='b'*40
        payload=json.dumps(data,sort_keys=True,separators=(',',':')).encode('ascii')
    with pytest.raises(RunnerManifestError):check(root,payload,modules)


def test_manifest_digest_is_external_approval_not_self_declared(tmp_path):
    root=package(tmp_path);payload=runner_manifest(root,'a'*40)
    with pytest.raises(RunnerManifestError):
        verify_runner_manifest(payload,approved_sha256='0'*64,approved_tree='a'*40,package_root=root,loaded_modules={})


def test_bad_manifest_stops_parent_before_reservation_or_process(tmp_path):
    s,host,state,clock,mutex=compose(tmp_path)
    s.runner_manifest_bytes+=b' '
    with pytest.raises(RunnerManifestError):prepare(s,mutex)
    assert host.calls==[]
    assert s.authority._open().execute('SELECT count(*) FROM run_v3').fetchone()[0]==0
