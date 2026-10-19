"""Decision-B owner alerts: local durable facts, never a retry authority."""
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import re

from .mvp_session_authority import canonical_v3, digest_v3

BLOCKERS = frozenset((
    'UPDATE_REQUIRED', 'APP_NOT_READY', 'UNSUPPORTED_UI', 'UNKNOWN_GAME_SCREEN',
    'LOGIN_OR_ACCOUNT_ACTION_REQUIRED', 'RECONNECT_REQUIRED', 'PACKAGE_METADATA_CONFLICT',
    'WRONG_ACCOUNT', 'WRONG_INSTANCE', 'WINDOW_BINDING_UNCERTAIN',
))


def _need(value):
    if not value:
        raise RuntimeError('OWNER_INTERVENTION_EVIDENCE_INVALID')


@dataclass(frozen=True, slots=True)
class OwnerIntervention:
    run_nonce: str
    account_ref: str
    blocker: str
    timestamp_ns: int
    candidate_tree: str
    profile_digest: str
    evidence_digest: str
    player_state: str
    event: str = 'OWNER_APP_UPDATE_OR_INTERVENTION_REQUIRED'
    package_metadata: str = 'UNAVAILABLE'
    gameplay_continued: bool = False
    gem_spend: bool = False

    def __post_init__(self):
        for value, length in ((self.run_nonce,32),(self.account_ref,32),
                              (self.candidate_tree,40),(self.profile_digest,64),(self.evidence_digest,64)):
            _need(type(value) is str and re.fullmatch('[0-9a-f]{%d}' % length,value) is not None)
        _need(type(self.blocker) is str and self.blocker in BLOCKERS
              and type(self.timestamp_ns) is int and self.timestamp_ns >= 0
              and type(self.player_state) is str and self.player_state in ('RUNNING','RETIRED','UNRESOLVED')
              and self.event == 'OWNER_APP_UPDATE_OR_INTERVENTION_REQUIRED'
              and self.package_metadata in ('UNAVAILABLE','OBSERVED','CONFLICT')
              and self.gameplay_continued is False and self.gem_spend is False)

    def payload(self):
        OwnerIntervention(**asdict(self))
        return canonical_v3(asdict(self))

    def message(self):
        """Sanitized future-adapter payload. No endpoint or sending capability."""
        value = json.loads(self.payload())
        value['instruction'] = ('Owner intervention required: provide/install an updated approved APK '
            'or resolve the reported blocker, then authorize a fresh run. No Gem spend or gameplay continuation occurred.')
        return value


class OwnerInterventionStore:
    """Existing protected Win32 write-through port, exact readback and no overwrite.

    Called under the lifecycle mutex after revocation. File is scoped to this
    run; a partial write remains an unresolved conflict, never a retry trigger.
    Discord transport deliberately is not constructed here.
    """
    def __init__(self, project, *, port=None):
        from .win32_state_io import Win32StateFilePort, NativeWin32StateApi
        self.project = Path(project).resolve(strict=True)
        self.port = port if port is not None else Win32StateFilePort(self.project, NativeWin32StateApi())

    def _read(self, path):
        handle = self.port.open_read_exclusive_no_reparse(path)
        try:
            return self.port.read_bounded(handle, 8192)
        finally:
            self.port.close(handle)

    def persist(self, event):
        _need(type(event) is OwnerIntervention)
        payload = event.payload()
        path = self.project / 'var' / ('owner-intervention-' + event.run_nonce + '.json')
        try:
            prior = self._read(path)
        except FileNotFoundError:
            handle = self.port.create_new_temp_write_through(path)
            try:
                _need(self.port.write(handle, memoryview(payload)) == len(payload))
                self.port.flush(handle)
            finally:
                self.port.close(handle)
            prior = self._read(path)
        _need(prior == payload)
        return digest_v3(prior)
