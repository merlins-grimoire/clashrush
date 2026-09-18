# Control-plane-only MVP donor inventory

## Scope

The installed local mode validates opaque account configuration, persists a stopped schema-v2 `READY` lifecycle value, reports the next protected slot, reports every excluded runtime capability as `UNAVAILABLE`, and denies dispatch. It contains no capture, image persistence, Home classification, BlueStacks execution, gameplay input/action, spending, network/Discord, or credential port.

## Donor inspection

| Source and immutable revision | Candidate paths, callers, and tests inspected | License | Decision |
|---|---|---|---|
| BasePilot `4ede1efd220ffc79a5b490cfd3788b44d2584da4` / tree `0553306bfd30a32e31e427a0b77ff22c41f55901` | `README.md:31-43,87-106`; `app/core/upgrader.py:310-350`; caller `app/core/bot.py:387-409`; no tracked donor test suite | MIT (`LICENSE`) | Reject reuse. `MODE_DRY` suppresses only the selected upgrade execution after `UpgradeAdvisor.scan()`; the surrounding bot still captures, navigates, farms, and clicks. It is not a complete no-runtime control mode and would weaken this MVP's zero-port boundary. |
| CoC_Bot `a5c943afed0ed3b9abedbbc228b0889145ecaf24` / tree `d79368fbe550036f1542883f18434e318016b279` | `src/configs.template.py`; callers `src/launch.py` and worker loops in `src/coc_bot.py`; no automated donor tests | MIT (`LICENSE`) | Reject reuse. Optional web/notification flags do not disable ADB/device workers, capture, or gameplay. There is no compatible fail-closed control-only seam. |
| ClashAutomation `c41fe12a6df051e241c695b71b6859286e24c612` / tree `b549901e7ca8b871a17265517d730f0b3c84a618` | `CLAUDE.md`; read-only probe `dev_tools/live_detection_check.py`; production callers `main.py`/`utils/clash_base.py`; tests `tests/test_orchestration.py`, `tests/test_vision_and_config.py`, `tests/test_detection_robustness.py` | MIT (`LICENSE`) | Reject reuse. The probe avoids input but still attaches to a live window and captures a frame; the production seam imports configuration and runtime controllers. It cannot satisfy zero capture, process, or private-runtime access. |

No complete compatible licensed disabled/dry-run/control-mode seam exists in the pinned donors. No donor code is copied, so no new third-party notice is required.

## Largest compatible seam reused

The MVP reuses the already-reviewed local immutable configuration and lifecycle value seam instead of creating another identity model:

- `configuration_v2.py`: `AccountKey`, `AccountRecord`, `ConfigurationGeneration`, and `SchemaV2Configuration` validate 1-10 ordered opaque accounts and generation identity.
- `lifecycle_state_v2.py`: `ReadyV2`, `encode_state_v2`, and `decode_state_v2` own canonical stopped lifecycle encoding and reject `ACTIVE` state.
- Production caller: `control_plane_mvp.ControlPlane`.
- Matching proof: `tests/test_control_plane_mvp.py`, plus the existing configuration/lifecycle suites.

The due scheduler and SQLite journal remain preserved but are not admitted by this mode. `schedule_next()` is read-only: it reports the slot following the stopped `READY` tie cursor and returns `dispatch_allowed=False`; it cannot advance lifecycle, create an admission, launch a process, or produce a gameplay plan.

## Installed boundary

`pyproject.toml` binds `clash-rush-rebuild` only to `clash_rush_rebuild.control_plane_cli:main`. The CLI vocabulary is exactly `setup`, `status`, and `schedule`. Status reports `gameplay=UNAVAILABLE` and `readiness=UNAVAILABLE`; schedule reports `dispatch=DENIED reason=GAMEPLAY_UNAVAILABLE` without printing account keys.

The former readiness design documents are retained under `research/parked/` with an explicit non-production warning. Their failed candidate commit/tree is not copied or integrated.
