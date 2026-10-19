"""Synthetic preparation boundaries only; no native observations or admission."""
import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / 'tools/attack_native_readiness.py'
_SPEC = importlib.util.spec_from_file_location('attack_native_readiness', _PATH)
readiness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(readiness)


def supplied(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    code = tmp_path / 'code'
    module = code / 'src/clash_rush_rebuild/mvp_deployment_profile.py'
    module.parent.mkdir(parents=True)
    module.write_bytes(b'# synthetic provenance only\n')
    deployment = source / 'c-correction/export/offline-pack/private/deployment'
    (deployment / 'templates').mkdir(parents=True)
    asset = b'synthetic asset; collect checks digest, actual loader checks PNG'
    (deployment / 'templates/marker.png').write_bytes(asset)
    data = dict(schema=3, reviewed_tree=readiness.ATTACK_TREE, runtime_reviewed=False,
                assets={'marker': {'file': 'marker.png', 'sha256': readiness.sha256(asset)}})
    (deployment / 'profile.json').write_bytes(readiness.canonical(data))
    result = dict(profile_reviewed_tree=readiness.ATTACK_TREE,
                  module_sha256=readiness.sha256(module.read_bytes()))
    (source / 'c-correction/export/results.json').write_bytes(readiness.canonical(result))
    frames = []
    for label in ('stable_home', 'battle_menu_fee', 'army_review_1', 'army_review_2'):
        raw = ('synthetic-' + label).encode()
        (source / (label + '.png')).write_bytes(raw)
        frames.append(dict(label=label, file=label + '.png', sha256=readiness.sha256(raw)))
    (source / 'INPUT_PROVENANCE.json').write_bytes(readiness.canonical(dict(frames=frames)))
    return source, code, data


def test_verified_collection_detaches_and_does_not_write(tmp_path):
    source, code, expected = supplied(tmp_path)
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    data, files, frames = readiness.collect(source, code)
    assert data == expected and len(frames) == 4
    assert files['original/profile.json'] == readiness.canonical(expected)
    data['runtime_reviewed'] = True
    assert json.loads(files['original/profile.json'])['runtime_reviewed'] is False
    assert before == {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file()}


@pytest.mark.parametrize('fault', ['frame', 'asset', 'code', 'tree', 'runtime', 'canonical', 'asset_path', 'frames'])
def test_bad_provenance_rejects_before_copy(tmp_path, fault):
    source, code, data = supplied(tmp_path)
    deployment = source / 'c-correction/export/offline-pack/private/deployment'
    if fault == 'frame':
        (source / 'stable_home.png').write_bytes(b'changed')
    elif fault == 'asset':
        (deployment / 'templates/marker.png').write_bytes(b'changed')
    elif fault == 'code':
        (code / 'src/clash_rush_rebuild/mvp_deployment_profile.py').write_bytes(b'changed')
    elif fault == 'frames':
        (source / 'INPUT_PROVENANCE.json').write_bytes(readiness.canonical({'frames': []}))
    elif fault == 'canonical':
        (deployment / 'profile.json').write_bytes(readiness.canonical(data) + b' ')
    else:
        if fault == 'tree': data['reviewed_tree'] = 'a' * 40
        elif fault == 'runtime': data['runtime_reviewed'] = True
        else: data['assets']['marker']['file'] = '../marker.png'
        (deployment / 'profile.json').write_bytes(readiness.canonical(data))
    with pytest.raises(ValueError):
        readiness.collect(source, code)


@pytest.mark.parametrize('relative', ['../escape', 'a/../../escape'])
def test_relative_escape_denied(tmp_path, relative):
    with pytest.raises(ValueError):
        readiness.confined(tmp_path, relative)


def test_absolute_path_denied(tmp_path):
    with pytest.raises(ValueError):
        readiness.confined(tmp_path, tmp_path / 'file')


def test_reparse_component_denied(tmp_path, monkeypatch):
    # Inject filesystem metadata only; creating a junction is unnecessary.
    original = Path.lstat
    marker = tmp_path / 'junction'
    marker.mkdir()
    class Metadata:
        st_mode = 0o040755
        st_file_attributes = 0x400
    monkeypatch.setattr(Path, 'lstat', lambda p: Metadata() if p == marker else original(p))
    with pytest.raises(ValueError, match='PATH_LINK'):
        readiness.confined(tmp_path, 'junction/file')


def test_read_size_bound(tmp_path):
    (tmp_path / 'file').write_bytes(b'123')
    with pytest.raises(ValueError, match='FILE_UNAVAILABLE'):
        readiness.read(tmp_path, 'file', 2)


def test_copy_rebinds_only_tree_without_runtime_authority(tmp_path, monkeypatch):
    source, code, data = supplied(tmp_path)
    project = tmp_path / 'project'
    project.mkdir()
    class Ignored:
        returncode = 0
    monkeypatch.setattr(readiness.subprocess, 'run', lambda *a, **k: Ignored())
    pack = readiness.prepare(source, project, code, 'b' * 40)
    prepared = json.loads((pack / 'offline-pack/private/deployment/profile.json').read_bytes())
    assert prepared == dict(data, reviewed_tree='b' * 40)
    assert not (pack / 'offline-pack/private/deployment/provider.json').exists()
    manifest = json.loads((pack / 'manifest.json').read_bytes())
    assert manifest['runtime_admission'] is manifest['native_executed'] is False
    assert manifest['prepared_profile_sha256'] == readiness.sha256(readiness.canonical(prepared))
    for path, digest in manifest['files'].items():
        assert readiness.sha256((pack / path).read_bytes()) == digest
    with pytest.raises(ValueError, match='DESTINATION_EXISTS'):
        readiness.prepare(source, project, code, 'b' * 40)


def test_tracked_destination_denied(tmp_path, monkeypatch):
    source, code, _ = supplied(tmp_path)
    class NotIgnored:
        returncode = 1
    monkeypatch.setattr(readiness.subprocess, 'run', lambda *a, **k: NotIgnored())
    with pytest.raises(ValueError, match='DESTINATION_NOT_IGNORED'):
        readiness.prepare(source, tmp_path, code, 'b' * 40)
    assert not (tmp_path / 'private').exists()


@pytest.mark.parametrize('tree', ['x' * 40, 'a' * 39, 'A' * 40, None])
def test_tree_literal_not_repaired(tmp_path, tree):
    with pytest.raises(ValueError, match='TREE_INVALID'):
        readiness.prepare(tmp_path, tmp_path, tmp_path, tree)


def test_query_is_future_tokens_not_observed_evidence():
    query = readiness.query_specification()
    assert query['executed'] is False
    assert query['status'] == 'STRICT_GUEST_BUILD_PROVIDER_DEFERRED_BY_OWNER'
    assert query['tails'][-1] == ['-s', '<discovered-serial>', 'shell', 'dumpsys', 'package',
                                  'com.supercell.clashofclans']
    assert query['package_reads'] == 2
    assert 'version_name' not in query and 'version_code' not in query
    assert 'DENIED' in query['activation']


def test_missing_input_matrix_does_not_turn_into_pass():
    missing = readiness.missing_inputs()
    assert all(row['fields'] and row['source'] and row['assertion'] for row in missing)
    assert {row['status'] for row in missing} == {'BLOCKED_MISSING_INPUT', 'FUTURE_NATIVE_UNPROVEN'}
    assert 'provider.account_ref' in {field for row in missing for field in row['fields']}
    fields = {field for row in missing for field in row['fields']}
    assert {'provider.slot_id', 'supported_home', 'native_mapping', 'source_screen_witnesses'} <= fields
    assert not fields & {'source_layout.build', 'provider.version_code', 'provider.account_template',
                         'provider.account_roi', 'provider.account_threshold'}


def test_wrong_import_root_denied_before_private_input(tmp_path):
    with pytest.raises(ValueError, match='WRONG_IMPORT_ROOT'):
        readiness.validate(tmp_path, tmp_path)


def test_console_failure_never_echoes_private_exception(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(readiness.sys, 'argv', ['offline', '--source', str(tmp_path),
        '--project', str(tmp_path), '--code-root', str(tmp_path), '--tree', 'a' * 40])
    def fail(*args):
        raise ValueError('sensitive payload must not escape')
    monkeypatch.setattr(readiness, 'prepare', fail)
    assert readiness.main() == 1
    assert capsys.readouterr().out == '{"overall":"OFFLINE_CHECK_FAILED"}\n'


def test_project_reparse_root_rejected_before_resolution(tmp_path, monkeypatch):
    original = Path.lstat
    class Metadata:
        st_mode = 0o040755
        st_file_attributes = 0x400
    monkeypatch.setattr(Path, 'lstat', lambda p: Metadata() if p == tmp_path else original(p))
    with pytest.raises(ValueError, match='PATH_LINK'):
        readiness.prepare(tmp_path, tmp_path, tmp_path, 'b' * 40)


def test_query_spec_matches_reviewed_transport_constants_without_import():
    import ast
    path = _PATH.parents[1] / 'src/clash_rush_rebuild/mvp_installed_build.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    def value(node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            return ast.literal_eval(node.left) * ast.literal_eval(node.right)
        return ast.literal_eval(node)
    constants = {node.targets[0].id: value(node.value)
                 for node in tree.body if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in {'PACKAGE', 'MAX_OUTPUT', 'READ_BUDGET'}}
    query = readiness.query_specification()
    assert query['package'] == constants['PACKAGE']
    assert query['budget_seconds'] == constants['READ_BUDGET']
    assert query['max_output_bytes'] == constants['MAX_OUTPUT']
    assert 'four reads' in query['package_reads_scope']
