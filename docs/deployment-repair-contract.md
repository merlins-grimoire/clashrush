# Mixed-card deployment repair contract (candidate)

Base: ed3b2b5b66a9395d35c14353ebbcc619d763d137. This contract describes the new
candidate code, not completed S004 evidence. The previous owner-assisted run
must not be reclassified as autonomous.

## Ownership

The CLI delegates attacks to `mvp_deployment_worker.run_supervised_attack`.
The child requires its parent-held metadata lease plus the existing durable
one-use, exact-tree/configuration/READY admission. The existing host mutex,
suspended Job-owned launch, binding checks and owned stop remain authoritative.
Readiness-only startup is not this attack purpose.

The parent gives the worker 270 seconds, leaving an additional 30-second
retirement allowance in the whole 300-second supervision envelope. The engine
admits no new gesture after the earlier of its own 240-second visit bound and
the parent's deadline minus 30 seconds. Existing runtime cleanup can fail;
uncertainty never becomes a successful receipt merely because time elapsed.
The monotonic execution bound is distinct from the existing wall-time admission
expiry. No first-pass or per-page deadline reset exists.

## Data and recognition

Full frames and derived live image arrays are transient. The observer clears
its writable frame and emits only typed scalar observations. Private templates
are bounded, immutable copies loaded through validated paths and SHA-256 hashes.
An exact tree and profile digest are bound to the grant. No template is admitted
by its donor name alone.

A complete card inventory requires explicit left/right edge evidence and unique
ordered overlaps. Duplicate/ambiguous identity, clipped count, absent card,
invalid type, or unexplained state change denies deployment. Gap heuristics,
missing x glyphs, default one, and color-only exhaustion are prohibited.

Troop/spell quantities must be exact bounded integers. Zero requires a matching
terminal-card appearance. Hero/clan terminal state means deployed/depleted—not
lack of multiplicity or recoloring alone. Card selection must be visibly proved.

## Inputs and policy

An Intent binds current card, screen/view, normalized target, existing action
label and absolute deadline. Delivery runs fresh proof immediately before down,
checks the durable run gate and intervention health, and pairs every down with
owned up cleanup. Timed waits never imply successful deployment.

Troops receive short holds with a 25-second cumulative per-card ceiling.
Hero/clan singletons receive one placement. Spells receive exactly one tap per
acknowledged decrement, with two observations agreeing before another cast.
All discovery, matching, scrolling and execution consume the same <=60-second
deployment budget. Reaching the budget while units remain is incomplete.

ClashAutomation's ray/edge-density result proposes ground. A separately admitted
visible exclusion boundary and playfield must establish legal points; all
marked small exclusions remain forbidden. No clamped-point, configured-point,
random, excess-tap or empty-center fallback is allowed.

Lightning targets approved current enemy-structure evidence; Earthquake targets
approved wall/structure-cluster evidence; Rage/Heal require applicable current
friendly/injury context and exclusion of active effects. Missing useful spell
conditions permit bounded read-only observation, never a speculative cast.
No type or damage statistic is inferred by a language/vision model at runtime.

## Completion

CAPTURE_BAR -> DISCOVER_CARDS -> CLASSIFY -> COMPLETE_COVERAGE -> SELECT ->
TYPED_DEPLOY -> VERIFY_DECREMENT -> SCROLL_OR_RECHECK -> VERIFY_ALL_TERMINAL ->
END_BATTLE -> OPTIONAL_BATTLE_CONFIRM -> RESULT -> RETURN_HOME -> VERIFY_HOME ->
OWNED_STOP -> VERIFY_RETIREMENT -> FINAL_AUTONOMOUS_RECEIPT.

End begins immediately after complete proof, not after a natural timer. Okay is
only the explicitly observed End confirmation, never a generic dialog action.
Unknown/reward/spending controls deny new gameplay input. Manual/foreign input,
unhealthy intervention observation or an unexplained result cannot satisfy
S004. Release and owned retirement still occur on failure.

The versioned receipt stores inventory count, consumed spells, remaining units,
exit origin, Home proof, and intervention state separately from lifecycle
retirement. Legacy CONFIRMED remains legacy. Schema extension DDL and its version
are committed atomically by explicit Run/Resume; read-only Status does not
migrate. A rich proof is required before the attack adapter can report CONFIRMED;
final autonomy additionally requires the existing successful retirement receipt.

## Promotion gates

The new unit suite is not canonical Windows verification. Hermetic dependency
resolution, canonical tests, current private calibration, forbidden-screen hard
negatives, lifecycle/hook/ctypes fault testing, privacy review and independent
exact-tree review all precede any separately authorized live trial.
