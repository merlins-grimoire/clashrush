"""Sanitized durable alerts cannot replay game operations."""
from dataclasses import replace
from pathlib import Path
import pytest
from clash_rush_rebuild.mvp_owner_intervention import (
    BLOCKERS, OwnerIntervention, OwnerInterventionStore,
)


class Files:
    def __init__(self): self.data = {}; self.flushes = 0
    def open_read_exclusive_no_reparse(self, path):
        if path not in self.data: raise FileNotFoundError
        return path
    def read_bounded(self, file, limit): return self.data[file]
    def close(self, file): pass
    def create_new_temp_write_through(self, path):
        if path in self.data: raise FileExistsError
        self.data[path] = b''
        return path
    def write(self, file, value):
        self.data[file] += bytes(value)
        return len(value)
    def flush(self, file): self.flushes += 1


def event(reason='UPDATE_REQUIRED'):
    return OwnerIntervention('a'*32,'b'*32,reason,1,'c'*40,'d'*64,'e'*64,'RUNNING')


@pytest.mark.parametrize('reason', sorted(BLOCKERS))
def test_alert_is_local_idempotent_and_sanitized(tmp_path,reason):
    port = Files()
    store = OwnerInterventionStore(tmp_path,port=port)
    value = event(reason)
    assert store.persist(value) == store.persist(value)
    assert port.flushes == 1
    message = value.message()
    assert message['blocker'] == reason and message['gameplay_continued'] is False
    assert message['gem_spend'] is False and message['package_metadata'] == 'UNAVAILABLE'
    assert not any(x in message for x in ('player_tag','instance_name','screenshot','credential'))
    assert not hasattr(store,'retry') and not hasattr(store,'send')
    with pytest.raises(RuntimeError): store.persist(replace(value,evidence_digest='f'*64))


def test_partial_write_and_readback_fail_closed(tmp_path):
    port=Files(); store=OwnerInterventionStore(tmp_path,port=port)
    port.write=lambda file,value: 1
    with pytest.raises(RuntimeError): store.persist(event())
    with pytest.raises(RuntimeError): store.persist(event())


@pytest.mark.parametrize('change', [dict(account_ref='private-name'), dict(blocker='GENERIC_RETRY'),
    dict(gameplay_continued=True),dict(gem_spend=True),dict(player_state='PROBABLY_CLOSED')])
def test_alert_rejects_private_or_untruthful_fields(change):
    with pytest.raises(RuntimeError): replace(event(),**change)
