# Slice 1 lifecycle architecture decision

## Status

Proposed replacement for the rejected snapshot-owned shutdown design. Implementation resumes only after this decision passes static architecture review.

## Why the first design is rejected

All four pinned codebases were searched for Windows Job Objects, suspended process creation, creation-time process identity, complete Toolhelp enumeration validation, and recursive process-tree ownership.

- BasePilot has no emulator process-tree supervisor; its `stop()` only stops its bot loop.
- CoC_Bot `src/utils.py:979-1000` uses an Android-debug poweroff first and otherwise terminates one cached PID with psutil.
- ClashAutomation `utils/game_program_controller.py:30-45` fuzzy-matches one process and terminates it.
- Published Clash Rush `src/clash_rush/bluestacks_launcher.py:329-335,414-416` enumerates player PIDs and force-stops one PID.
- None uses a Windows Job Object, suspended creation, a retained process handle, or complete descendant/HWND stop proof.

Toolhelp snapshots cannot establish ownership after a descendant is reparented, and a numeric PID can be reused. Repeatedly hardening that model would not close those races.

## Selected architecture

Use a locally implemented Windows Job Object lifecycle boundary. Donor code remains useful for exact BlueStacks configuration resolution, title/PID/HWND binding, and PrintWindow capture, but not for process ownership or shutdown.

Before reading player state, launch configuration, or lifecycle state, acquire `Global\ClashRushRebuildLifecycle-v1`. Create it with a protected DACL granting full access only to `SYSTEM` and the current operator SID; never use a null or default DACL. Hold it through verified stop and the final durable state commit. A second runner in any Windows session fails before discovery or state access. Treat `WAIT_ABANDONED` as a consistency fault: create no process, inspect durable state plus complete host process/window state read-only, and require operator reconciliation. An unsynchronized external BlueStacks launch cannot acquire this mutex, so complete player enumeration before and after launch remains mandatory.

### Launch transaction

1. Acquire the host-wide mutex and reject `WAIT_ABANDONED` as described above.
2. Read and validate durable lifecycle state. Continue only from exact `READY(next_slot)`.
3. Prove a complete Toolhelp enumeration ended with `ERROR_NO_MORE_FILES`; any other `Process32FirstW` or `Process32NextW` result fails closed.
4. Refuse launch if any `HD-Player.exe` identity exists.
5. Durably commit and read back `ACTIVE(slot, run_nonce, blocked_reason=null)` using the persistence protocol below. This must finish before any process or Job is created.
6. Create an unnamed Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Enable neither breakaway flag.
7. Call `CreateProcessW` for exact `HD-Player.exe --instance <internal_name>` with `CREATE_SUSPENDED` and inherited handles disabled.
8. Record the retained process handle, PID, and creation `FILETIME` before resuming it.
9. Assign the still-suspended process to the Job Object.
10. Require `ResumeThread` to return the exact prior suspend count of one. Resume once and close the thread handle. Retain the process and Job handles through verified stop.
11. If creation, identity capture, Job configuration, assignment, resume, thread-handle closure, post-resume enumeration, binding, or capture readiness fails, terminate through the private Job—or the retained suspended process handle if assignment never succeeded. Wait for the original process handle, prove Job active count zero when applicable, invalidate captured HWNDs, and prove complete player absence. Keep `ACTIVE` with a durable closed reason code. Never return a binding or advance the slot.

Creating the player suspended prevents it from creating an unowned child before Job assignment. Windows normally places child processes in the parent's Job. No breakaway option is granted.

### Runtime identity

- Bare launch PIDs never authorize action. Use immutable `ProcessIdentity(pid, creation_time_100ns)` values.
- `PlayerBinding` carries that identity plus exact root/render HWNDs and geometry.
- Every PID observation is paired with creation `FILETIME`; numeric PID reuse produces a different identity and fails closed.
- The host retains the original process handle and private Job handle in memory. Handles and display names are never persisted or logged.
- Root and render HWNDs must remain owned by the exact process identity, and render must remain under the exact root.

Job membership snapshots use `JOBOBJECT_BASIC_PROCESS_ID_LIST` with bounded buffer-resize retries. Accept only when `NumberOfProcessIdsInList == NumberOfAssignedProcesses`. For every returned PID, open and retain a query/synchronize process handle, read creation `FILETIME`, call `IsProcessInJob` against the private Job, and reject any failure or mismatch. Re-query until two consecutive complete `(pid, creation_time_100ns)` sets match. Numeric Job-member PIDs alone never authorize diagnostics, proof, or termination.

### Safe-screen proof

`stop()` accepts `None` or an exact immutable `SafeScreenProof`, never a boolean. The proof binds process identity, root/render HWNDs, capture nonce, and a short monotonic deadline. Immediately before `WM_CLOSE`, revalidate retained-process identity, HWND ownership, render-to-root ancestry, Job membership, deadline, and proof-to-binding nonce. A mismatch skips `WM_CLOSE` and proceeds to Job termination. Slice 1 has no safe-screen classifier, so its production caller always supplies `None` and cannot issue `WM_CLOSE`.

### Stop transaction

1. Preserve bound HWNDs and obtain a complete, stable, handle-backed Job membership snapshot.
2. Slice 1 skips `WM_CLOSE`. A future same-binding proof may authorize it only after immediate revalidation.
3. If the Job is not empty, call `TerminateJobObject`; never terminate through a freshly opened numeric PID.
4. Wait on the retained original process handle and poll Job accounting until active-process count is zero.
5. Require every captured HWND to be invalid. HWND reuse remains a failure because `IsWindow` stays true.
6. Re-enumerate all `HD-Player.exe` identities with complete Toolhelp termination validation. Any player blocks progression.
7. Durably replace lifecycle state with `READY(next_slot)` using the protocol below, read it back, close process and Job handles, issue `StopRecord`, and release the mutex.
8. Any API error, timeout, malformed identity, non-empty Job, surviving/reused HWND, unexpected player, or state-write failure leaves the last durable `ACTIVE` state or durably sets its `blocked_reason`. It never writes names, PIDs, HWNDs, tags, or raw exception text.

### Crash-durable lifecycle state

The single state file has two exact variants: `READY(next_slot)` and `ACTIVE(slot, run_nonce, blocked_reason)`. `ACTIVE` is durably committed before process creation and `READY` only after complete stop proof. A failure changes only `ACTIVE.blocked_reason`; it never discards `slot` or `run_nonce`. On restart, every `ACTIVE` state, surviving player, or unprovable state blocks launch. Clearing `ACTIVE` requires a separate operator-controlled reconciliation command that performs read-only process/window checks and records an audit event. Normal startup cannot clear it.

`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` makes loss of the final Job handle terminate Job members. It does not authorize cursor advancement; after a crash the durable state remains `ACTIVE`.

### Windows persistence protocol

Every transition uses this exact protocol beneath project `var/`:

1. Serialize bounded canonical UTF-8 JSON with exact fields and no values beyond slot, random run nonce, next slot, and closed reason code.
2. Create a same-directory temporary file with `CreateFileW` and `FILE_FLAG_WRITE_THROUGH`; reject reparse-point destinations and keep the resolved path beneath project `var/`.
3. Write every byte, call `FlushFileBuffers`, and successfully close the temporary handle.
4. Replace the target with `MoveFileExW(MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)`.
5. Reopen without write sharing, read completely, and require byte equality plus successful schema parsing.
6. On any failure, do not create a process or report a transition. Before launch this leaves `READY`; after `ACTIVE` it leaves the previous durable `ACTIVE`, which blocks restart.

Synthetic tests inject the filesystem port and prove ordering `write -> FlushFileBuffers -> write-through replace -> read-back -> CreateProcessW`, plus short writes, flush/replace/read-back failures, stale temporary files, malformed state, and crash points around every operation.

## Compatibility risk and live gate

Microsoft documents that a Job normally contains child processes unless breakaway is allowed. It also warns that software designed to assign itself to another Job may fail when breakaway is forbidden. BlueStacks compatibility is not assumed.

After implementation, clean-export tests, privacy scanning, and independent review, request owner approval for one inert trial. It passes only if BlueStacks starts normally, Job members remain contained, capture health succeeds, Job termination empties the Job, the retained process handle signals, HWNDs invalidate, and no player remains. Failure ends the trial without snapshot-owned fallback.

## Rejected alternatives

- CoC_Bot's Android-debug shutdown: prohibited and does not prove the host process tree.
- Single-PID `TerminateProcess`, `Stop-Process`, psutil, or `taskkill /T`: vulnerable to descendants or PID reuse.
- Repeated Toolhelp polling/suspension: cannot recover a child already reparented before observation.
- Allowing Job breakaway: defeats complete ownership and stop proof.

## Authoritative platform references

- Microsoft, Job Objects: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- Microsoft, AssignProcessToJobObject: https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
- Microsoft, QueryInformationJobObject: https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject
- Microsoft, Process32NextW: https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-process32nextw
