"""Concrete provider -> S004 consumer; source leaves only are synthetic."""
from dataclasses import replace
import pytest
from test_policy_installed_closure import setup,DUMP
from test_s004_source_images import source_pack
from clash_rush_rebuild.lifecycle import PlayerBinding
from clash_rush_rebuild.mvp_deployment import DeploymentError
from clash_rush_rebuild.mvp_deployment_profile import DeploymentProfile,require_installation
from clash_rush_rebuild.mvp_deployment_integration import InstalledProfileReader
from test_policy_finalization_v3 import binding_v3


def compose():
    m,low,build,provider=setup()
    data,assets=source_pack()
    data['source_layout']['build']='18.600.7'
    profile=DeploymentProfile(data,assets,'b'*64)
    player=PlayerBinding(low.player,low.player,11,12,640,360,'c'*32)
    adapter=InstalledProfileReader(provider=provider,build_binding=build,
        player_binding=player,profile=profile,version_code=180600008,live_gate=lambda:True)
    return low,build,player,profile,adapter


def test_real_provider_is_consumed_before_profile_acceptance():
    low,build,player,profile,adapter=compose()
    require_installation(profile,player,adapter)
    assert len(low.cleaned)==2 and not low.running
    assert adapter.provider._pending is None
    assert len(adapter.last_digest)==64
    first=adapter.last_digest
    run=replace(binding_v3(),run_nonce=build.run_nonce,generation=build.generation,profile_digest=profile.digest)
    assert adapter.evidence_digest(run)!=first  # Fresh transport/time bound evidence.
    assert len(low.cleaned)==4 and adapter.provider._pending is None


@pytest.mark.parametrize('change',['pid','config','package','version','update','cleanup','expired','generation','other_binding'])
def test_consumer_revokes_on_change_without_baseline_fallback(change):
    low,build,player,profile,adapter=compose()
    require_installation(profile,player,adapter)
    if change=='pid':low.pid_change=True
    if change=='config':low.conf+=b'changed=true\n'
    if change=='package':low.dumps=[DUMP.replace('com.supercell.clashofclans','invalid.package')]
    if change=='version':low.dumps=[DUMP.replace('18.600.7','18.600.8')]
    if change=='update':low.dumps=[DUMP.replace('2026-10-06','2026-10-07')]
    if change=='cleanup':low.cleanup_ok=False
    if change=='expired':adapter.gate=lambda:False
    with pytest.raises(DeploymentError):
        if change=='generation':
            adapter.evidence_digest(replace(binding_v3(),generation=build.generation+1))
        else:
            require_installation(profile,object() if change=='other_binding' else player,adapter)
    assert adapter.revoked and adapter.provider._pending is None
    calls=len(low.commands)
    with pytest.raises(DeploymentError):require_installation(profile,player,adapter)
    assert len(low.commands)==calls
