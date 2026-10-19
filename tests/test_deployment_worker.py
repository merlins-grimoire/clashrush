import os
import time
import pytest
from clash_rush_rebuild.mvp_deployment import DeploymentError
from clash_rush_rebuild.mvp_deployment_worker import MouseLease, retire_mouse, require_worker_context


def test_lease_is_metadata_only_and_tracks_only_its_mouse():
    with MouseLease.create(time.monotonic()+5) as owner:
        with MouseLease.open(owner.name,expected_parent=os.getpid()) as child:
            assert child.active() and not child.held
            child.set_held(True); assert owner.held
            child.set_held(False); assert not owner.held
            owner.revoke();assert not child.active()
            with pytest.raises(DeploymentError):child.set_held(True)


def test_parent_never_takes_over_historical_release_ownership():
    calls=[]
    with MouseLease.create(time.monotonic()+5) as owner:
        owner.set_held(True)
        def fail(marker):calls.append(marker);raise RuntimeError('inert')
        assert retire_mouse(owner,fail) is False and owner.held
        assert retire_mouse(owner,lambda marker:calls.append(marker)) is False
        assert owner.held and calls==[]
        assert retire_mouse(owner,lambda marker:calls.append(marker)) is False
        assert calls==[]


def test_unowned_or_stale_lease_cannot_authorize():
    with MouseLease.create(time.monotonic()+5) as owner:
        with pytest.raises(DeploymentError):MouseLease.open(owner.name,expected_parent=os.getpid()+1)
    with pytest.raises(DeploymentError):require_worker_context()
