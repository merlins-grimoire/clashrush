"""Disposable transformations, never the real installed package."""
import importlib.util
from pathlib import Path
import pytest
from clash_rush_rebuild.mvp_legacy_decommission import (
    TOMBSTONE, copy_offline_fixture, plan_fixture, apply_fixture,
    verify_decommissioned, activate_installed,
)


def fixture(tmp_path):
    old = tmp_path/'old'
    old.mkdir()
    (old/'__init__.py').write_text('')
    (old/'native.py').write_text('def create(kernel):\n    return kernel()\n')
    (old/'native.pyc').write_bytes(b'synthetic cached executable')
    copied = copy_offline_fixture(old, tmp_path/'copy')
    plan = plan_fixture(copied,version='legacy-synthetic',source_tree='a'*40)
    journal=tmp_path/'journal';journal.mkdir()
    return copied,plan,journal


def test_whole_package_decommission_blocks_direct_module_and_cache(tmp_path):
    copied,plan,journal=fixture(tmp_path)
    calls=[]
    spec=importlib.util.spec_from_file_location('synthetic_old',copied.root/'native.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.create(lambda:calls.append('fake-create'))
    assert calls==['fake-create']
    result=apply_fixture(copied,plan,journal,stopped=lambda:True)
    assert (copied.root/'native.py').read_bytes()==TOMBSTONE
    assert (copied.root/'native.pyc').read_bytes()==b''
    with pytest.raises(RuntimeError,match='DECOMMISSIONED'):
        exec(compile((copied.root/'native.py').read_bytes(),'synthetic','exec'),{})
    assert apply_fixture(copied,plan,journal,stopped=lambda:True)==result
    verify_decommissioned(result)
    assert (tmp_path/'old/native.py').read_text().startswith('def create')


def test_unknown_bytes_and_live_state_deny_without_transform(tmp_path):
    copied,plan,journal=fixture(tmp_path)
    with pytest.raises(RuntimeError): apply_fixture(copied,plan,journal,stopped=lambda:False)
    assert not tuple(journal.iterdir())
    (copied.root/'native.py').write_text('changed')
    with pytest.raises(RuntimeError): apply_fixture(copied,plan,journal,stopped=lambda:True)
    assert (copied.root/'native.py').read_text()=='changed'


def test_reintroduced_legacy_bytes_invalidate_receipt(tmp_path):
    copied,plan,journal=fixture(tmp_path)
    result=apply_fixture(copied,plan,journal,stopped=lambda:True)
    (copied.root/'native.pyc').write_bytes(b'old code reintroduced')
    with pytest.raises(RuntimeError):verify_decommissioned(result)


def test_actual_activation_is_not_an_offline_side_effect():
    with pytest.raises(RuntimeError,match='SEPARATE_OWNER_GATE'):
        activate_installed()
