"""Actual SQLite tests, using the documented authority schema, never private data."""
import sqlite3
from types import SimpleNamespace
import pytest
from clash_rush_rebuild.mvp_deployment import DeploymentError, DeploymentResult
from clash_rush_rebuild.mvp_deployment_receipt import (
    upgrade_schema, bind_profile, require_profile, record_proof, read_proof,
    require_complete, autonomy_status,
)

# Deliberately minimal shape for helper transactions; schema equivalence is also
# tested against the real source schema by the Hermes exact-tree integration gate.
SCHEMA='''
CREATE TABLE runs (run_nonce TEXT PRIMARY KEY,purpose TEXT,game_outcome TEXT,
 retirement_outcome TEXT,final_acknowledged INTEGER,control_revision INTEGER);
CREATE TABLE control (singleton INTEGER PRIMARY KEY,mode TEXT,current_run_nonce TEXT,revision INTEGER);
CREATE TABLE transactions (transaction_ref TEXT PRIMARY KEY,run_nonce TEXT,phase TEXT);
'''
NONCE='1'*32; DIGEST='a'*64

class Authority:
    def __init__(self,c):self.c=c
    def _open(self):return self.c


def setup(path=':memory:'):
    c=sqlite3.connect(path,isolation_level=None);c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON');c.executescript(SCHEMA+'PRAGMA user_version=1;')
    upgrade_schema(c,SCHEMA)
    c.execute('BEGIN IMMEDIATE')
    c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)',(NONCE,'MVP_SINGLE_ACCOUNT_ATTACK',None,None,0,2))
    c.execute('INSERT INTO control VALUES (1,?,?,2)',('RUNNING',NONCE))
    c.execute('INSERT INTO transactions VALUES (?,?,?)',('tx',NONCE,'INPUT_STARTED'))
    bind_profile(c,NONCE,'MVP_SINGLE_ACCOUNT_ATTACK',DIGEST)
    c.execute('COMMIT')
    return Authority(c)


def result(complete=True):
    return DeploymentResult(complete,'AUTONOMOUS_DEPLOYMENT_COMPLETE' if complete else 'INTERVENTION',2,2,complete,True,complete,0 if complete else 2)


def test_schema_upgrade_is_repeatable_and_legacy_is_not_autonomous():
    a=setup();upgrade_schema(a.c,SCHEMA)
    assert a.c.execute('PRAGMA user_version').fetchone()[0]==2
    assert read_proof(a,'tx') is None and autonomy_status(a,'tx')=='UNVERIFIED'
    a.c.close()


def test_profile_is_bound_and_cannot_be_substituted():
    a=setup();require_profile(a,NONCE,DIGEST)
    with pytest.raises(DeploymentError):require_profile(a,NONCE,'b'*64)
    with pytest.raises(DeploymentError):bind_profile(a.c,NONCE,'MVP_SINGLE_ACCOUNT_ATTACK',DIGEST)
    a.c.close()


def test_home_cleanup_alone_cannot_promote_receipt():
    a=setup();a.c.execute("UPDATE runs SET game_outcome='CONFIRMED',retirement_outcome='SUCCEEDED',final_acknowledged=1")
    a.c.execute("UPDATE transactions SET phase='CONFIRMED'")
    assert autonomy_status(a,'tx')=='UNVERIFIED'
    with pytest.raises(DeploymentError):require_complete(a,'tx')
    a.c.close()


def test_proof_and_retirement_both_required():
    a=setup();record_proof(a,'tx',result(),DIGEST);require_complete(a,'tx')
    assert autonomy_status(a,'tx')=='PENDING'
    a.c.execute("UPDATE runs SET game_outcome='CONFIRMED',retirement_outcome='SUCCEEDED',final_acknowledged=1")
    a.c.execute("UPDATE transactions SET phase='CONFIRMED'")
    assert autonomy_status(a,'tx')=='AUTONOMOUS'
    with pytest.raises(DeploymentError):record_proof(a,'tx',result(),DIGEST)
    a.c.close()


def test_manual_outcome_persists_without_becoming_success(tmp_path):
    path=tmp_path/'proof.sqlite3';a=setup(path);record_proof(a,'tx',result(False),DIGEST)
    a.c.execute("UPDATE runs SET game_outcome='UNCERTAIN',retirement_outcome='SUCCEEDED',final_acknowledged=1")
    a.c.execute("UPDATE transactions SET phase='UNCERTAIN'")
    assert autonomy_status(a,'tx')=='INCOMPLETE';a.c.close()
    c=sqlite3.connect(path,isolation_level=None);c.row_factory=sqlite3.Row;b=Authority(c)
    upgrade_schema(c,SCHEMA);assert not read_proof(b,'tx').complete
    with pytest.raises(DeploymentError):require_complete(b,'tx')
    c.close()


def test_lost_run_authority_cannot_record_complete():
    a=setup();a.c.execute("UPDATE control SET mode='STOPPED',revision=3")
    with pytest.raises(DeploymentError):record_proof(a,'tx',result(),DIGEST)
    record_proof(a,'tx',result(False),DIGEST);a.c.close()


def test_unknown_or_tampered_schema_is_not_migrated():
    c=sqlite3.connect(':memory:',isolation_level=None)
    c.executescript(SCHEMA+'PRAGMA user_version=1; ALTER TABLE runs ADD COLUMN extra TEXT;')
    with pytest.raises(DeploymentError):upgrade_schema(c,SCHEMA)
    assert c.execute('PRAGMA user_version').fetchone()[0]==1;c.close()


def test_migration_failure_rolls_back_ddl_and_version():
    c=sqlite3.connect(':memory:',isolation_level=None);c.executescript(SCHEMA+'PRAGMA user_version=1;')
    def deny(action,a,b,_db,_source):
        if action==sqlite3.SQLITE_PRAGMA and a=='user_version' and b=='2':return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    c.set_authorizer(deny)
    with pytest.raises(DeploymentError):upgrade_schema(c,SCHEMA)
    c.set_authorizer(None)
    assert c.execute('PRAGMA user_version').fetchone()[0]==1
    assert c.execute("SELECT 1 FROM sqlite_master WHERE name='deployment_receipts'").fetchone() is None
    c.close()


def test_status_schema_validation_does_not_migrate_legacy_database():
    from clash_rush_rebuild.mvp_deployment_receipt import validate_schema
    c=sqlite3.connect(':memory:',isolation_level=None)
    c.executescript(SCHEMA+'PRAGMA user_version=1;')
    validate_schema(c,SCHEMA)
    assert c.execute('PRAGMA user_version').fetchone()[0]==1
    assert c.execute("SELECT 1 FROM sqlite_master WHERE name='deployment_receipts'").fetchone() is None
    c.close()
