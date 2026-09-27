"""Versioned deployment evidence inside the existing MVP authority database.

The patch adds this table/version atomically. Old CONFIRMED receipts retain their
old meaning and have no deployment proof. This module never rewrites lifecycle
state or retroactively infers autonomous completion from Home/retirement.
"""
from __future__ import annotations
import json
import re
import sqlite3
from .mvp_deployment import DeploymentError, DeploymentResult

_CREATE='''CREATE TABLE deployment_receipts (
 run_nonce TEXT PRIMARY KEY REFERENCES runs(run_nonce),
 profile_sha256 TEXT NOT NULL CHECK(length(profile_sha256)=64),
 evidence TEXT
) STRICT'''


def _manifest(connection):
    return tuple((r[0],r[1],r[2], ' '.join((r[3] or '').split())) for r in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))


def validate_schema(connection: sqlite3.Connection, base_schema: str) -> None:
    """Read-only constructor/Status validation; no implicit migration."""
    reference=sqlite3.connect(':memory:',isolation_level=None)
    try:
        reference.executescript(base_schema)
        version=connection.execute('PRAGMA user_version').fetchone()[0]
        if type(version) is not int or version not in (1,2):raise DeploymentError('SCHEMA_UNSUPPORTED')
        if version==2:reference.execute(_CREATE)
        if _manifest(connection)!=_manifest(reference):raise DeploymentError('SCHEMA_NONCANONICAL')
    except BaseException:raise DeploymentError('SCHEMA_NONCANONICAL') from None
    finally:reference.close()


def upgrade_schema(connection: sqlite3.Connection, base_schema: str) -> None:
    """One transaction for extension DDL and user_version; strict source schema.

    Called by the explicit Run/Resume grant operation, never read-only Status.
    `base_schema` is the tracked module's literal _SCHEMA, never database input.
    """
    reference=sqlite3.connect(':memory:',isolation_level=None)
    try:
        reference.executescript(base_schema)
        old=_manifest(reference)
        reference.execute(_CREATE)
        new=_manifest(reference)
        if connection.in_transaction: raise DeploymentError('SCHEMA_TRANSACTION_ACTIVE')
        connection.execute('BEGIN IMMEDIATE')
        version=connection.execute('PRAGMA user_version').fetchone()[0]
        if type(version) is not int or version not in (1,2): raise DeploymentError('SCHEMA_UNSUPPORTED')
        if _manifest(connection)!=(old if version==1 else new):raise DeploymentError('SCHEMA_NONCANONICAL')
        if version==1:
            connection.execute(_CREATE)
            connection.execute('PRAGMA user_version=2')
            if _manifest(connection)!=new: raise DeploymentError('SCHEMA_NONCANONICAL')
        connection.execute('COMMIT')
    except BaseException:
        if connection.in_transaction:connection.execute('ROLLBACK')
        raise DeploymentError('SCHEMA_UPGRADE_FAILED') from None
    finally:
        reference.close()


def _digest(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}',value) is None:
        raise DeploymentError('PROFILE_BINDING_INVALID')
    return value


def bind_profile(connection, run_nonce: str, purpose: str, digest: str | None) -> None:
    """Must run inside the same short transaction that INSERTs the run grant."""
    if connection.in_transaction is not True:raise DeploymentError('PROFILE_TRANSACTION_REQUIRED')
    if purpose=='MVP_SINGLE_ACCOUNT_ATTACK':
        _digest(digest)
        connection.execute('INSERT INTO deployment_receipts VALUES (?,?,NULL)',(run_nonce,digest))
    elif digest is not None:
        raise DeploymentError('PROFILE_PURPOSE_MISMATCH')


def require_profile(authority,run_nonce: str,digest: str) -> None:
    _digest(digest)
    row=authority._open().execute('SELECT profile_sha256 FROM deployment_receipts WHERE run_nonce=?',(run_nonce,)).fetchone()
    if row is None or row[0]!=digest:raise DeploymentError('PROFILE_BINDING_MISMATCH')


def record_proof(authority,transaction_ref: str,result: DeploymentResult,digest: str) -> None:
    if type(result) is not DeploymentResult:raise DeploymentError('RESULT_INVALID')
    _digest(digest)
    raw=json.dumps(result.public_payload(),sort_keys=True,separators=(',',':'))
    c=authority._open()
    try:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('''SELECT t.run_nonce,t.phase,d.profile_sha256,d.evidence,
             r.control_revision,c.mode,c.current_run_nonce,c.revision
             FROM transactions t JOIN runs r ON r.run_nonce=t.run_nonce
             JOIN deployment_receipts d ON d.run_nonce=t.run_nonce
             JOIN control c ON c.singleton=1 WHERE t.transaction_ref=?''',(transaction_ref,)).fetchone()
        if row is None or row[1]!='INPUT_STARTED' or row[2]!=digest or row[3] is not None:
            raise DeploymentError('PROOF_BINDING_MISMATCH')
        if result.complete and (row[5]!='RUNNING' or row[6]!=row[0] or row[7]!=row[4]):
            raise DeploymentError('AUTHORIZATION_LOST')
        c.execute('UPDATE deployment_receipts SET evidence=? WHERE run_nonce=? AND evidence IS NULL',(raw,row[0]))
        c.execute('COMMIT')
    except BaseException:
        if c.in_transaction:c.execute('ROLLBACK')
        raise DeploymentError('PROOF_COMMIT_FAILED') from None


def _decode(raw):
    if type(raw) is not str or len(raw)>2048:raise DeploymentError('PROOF_INVALID')
    try:
        data=json.loads(raw)
        if type(data) is not dict or data.pop('schema',None)!=1:raise DeploymentError('PROOF_INVALID')
        result=DeploymentResult(**data)
        if json.dumps(result.public_payload(),sort_keys=True,separators=(',',':'))!=raw:
            raise DeploymentError('PROOF_INVALID')
        return result
    except BaseException:raise DeploymentError('PROOF_INVALID') from None


def read_proof(authority,transaction_ref: str) -> DeploymentResult | None:
    try:
        row=authority._open().execute('''SELECT d.evidence FROM deployment_receipts d
            JOIN transactions t ON t.run_nonce=d.run_nonce WHERE t.transaction_ref=?''',(transaction_ref,)).fetchone()
        return None if row is None or row[0] is None else _decode(row[0])
    except DeploymentError:raise
    except BaseException:raise DeploymentError('PROOF_UNAVAILABLE') from None


def require_complete(authority,transaction_ref: str) -> DeploymentResult:
    result=read_proof(authority,transaction_ref)
    if result is None or not result.complete:raise DeploymentError('AUTONOMY_UNPROVED')
    return result


def autonomy_status(authority,transaction_ref: str | None = None) -> str:
    """Closed public scalar. Presence of a terminal receipt is not success."""
    try:
        c=authority._open()
        if transaction_ref is None:
            row=c.execute('''SELECT t.transaction_ref FROM transactions t
                JOIN control c ON c.current_run_nonce=t.run_nonce WHERE c.singleton=1''').fetchone()
            if row is None:return 'UNVERIFIED'
            transaction_ref=row[0]
        result=read_proof(authority,transaction_ref)
        if result is None:return 'UNVERIFIED'
        if not result.complete:return 'INCOMPLETE'
        row=c.execute('''SELECT t.phase,r.game_outcome,r.retirement_outcome,r.final_acknowledged
             FROM transactions t JOIN runs r ON r.run_nonce=t.run_nonce
             WHERE t.transaction_ref=?''',(transaction_ref,)).fetchone()
        if row is not None and tuple(row)==('CONFIRMED','CONFIRMED','SUCCEEDED',1):return 'AUTONOMOUS'
        return 'PENDING'
    except BaseException:return 'UNVERIFIED'
