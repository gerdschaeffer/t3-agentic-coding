# Usage-limit recovery for unattended work

Temporary quota exhaustion pauses the workflow and resumes automatically when
capacity returns. It is never approval, completion, or permission to change
models. Preserve the spec and review gates, partial work, and spending limits.

## Before starting

Check fresh provider/account/model usage and reset times when exposed, including
both session and weekly limits. Shared-account workers consume the same pool;
do not launch more work into a known exhausted pool. Missing usage is unknown,
not unlimited. Do not run paid model probes just to measure capacity.
Recheck exposed limits before expensive launches and inspect worker failure
results for quota errors; never poll active delegates to check their allowance.

Use T3's server-side `UsageLimitRecoveryWorker` as the watchdog. Verify
**Settings → Threads → Resume after usage limits** is enabled for the execution
environment. Inspect supported settings read-only; do not guess a setting from
`orchestrator_capabilities`, which reports models, not remaining quota. If the
setting is disabled, enable it through a supported configuration route when
authorized, or surface the setup requirement before unattended work begins.
The leader needs this protection too: a quota-blocked leader cannot monitor
itself. Keep the T3 host running and prevent sleep for the intended run using
the user's existing host/service setup. A skill cannot recover an offline host.

Keep the recovery checkpoint in a durable task artifact, not only conversation
history or a temporary directory subject to cleanup. Store the parent and task
IDs, failed run, provider/account/model and settings, spec revision and review
ledger, branch/HEAD and dirty-file inventory, partial-work state, original brief,
last confirmed check, error class, reset/retry time in UTC, and next action.
Record watchdog ownership and any scheduled task ID so resumes cannot duplicate
writers. Maintain this checkpoint before expensive stages, not only after failure.

## On a limit

Distinguish a provider-reported usage limit or transient 429 from authentication
failure, missing entitlement, exhausted prepaid credits, and this request's $3
soft ceiling. Only temporary limits enter automatic recovery. Do not buy credits,
enable overage, spend reset credits, bypass approvals, or switch accounts/models.
Follow the provider's retry/reset time; never infer a subscription reset from an
API price or invent a reset timestamp.

Mark the stage `waiting_for_quota` in the task record and save the checkpoint.
Allow unaffected work to finish when it is safe and within budget; hold dependent
work. Prefer a single status update with the next retry time over repeated
questions. A normal temporary quota pause needs no user answer overnight.

When T3 has armed native recovery, let it own the retry. Do not cancel or send
messages to the limited thread, stop/archive/settle it, or create a competing
schedule while recovery is wanted: those actions can cancel recovery or duplicate
it. Explicit cancellation and budget exhaustion use the cleanup below. This quota path
overrides sol-loop's ordinary cancel-and-restart advice for failed reviewers.
Keep the same review scope frozen until that reviewer finishes or recovery is
explicitly cancelled. Follow the delegate's `next`; never poll a running task.

## Fallback when native recovery cannot arm

Only for a confirmed quota-stopped worker, with no live child work or native
continuation pending, use one T3 `schedule_task` bound explicitly to the healthy
leader's current thread (`bindToCurrentThread: true`). This is a deferred quota
retry, never a watcher for a running delegate. Save the returned schedule ID and
next run time. Its prompt must name the recovery checkpoint and tell the leader
to check the saved pause before doing anything. Never create top-level watchdog
threads, shell polling loops, or repeated sleeping model calls.

Use the reported retry time plus a 60-second margin. With no trustworthy reset
time, defer for one hour; do not claim the quota will have reset. Use the
supported interval schedule with `everyMs` at least 60000; on its first wake,
disable that schedule before handling recovery so it acts as a single retry.
If still limited, save the new evidence and schedule the next eligible retry.
Keep at most one fallback schedule per request. For unknown reset times, stop
automatic retries after 24 hours from the first pause. All wake/model costs and
worker relaunches remain inside the original $3 and launch limits; neither a
quota reset nor a watchdog wake resets those budgets.

Do not schedule the blocked leader as its own fallback watchdog. If native
recovery cannot arm for the leader, scheduling is unavailable, the budget ends,
or a permanent error requires user action, preserve the checkpoint and report
that unattended recovery is blocked. Never promise a working watchdog without
confirming one is armed. When the request completes, is cancelled, reaches its
budget, or is handed back, disable owned fallback schedules and cancel any
request-owned native continuations through supported controls. Use `task_cancel`
for delegated work being abandoned; do not disable global auto-resume for other
requests. If cleanup cannot be confirmed, report the remaining armed recovery
explicitly. Disable an owned fallback before switching leader providers too.
A scheduler's successful delivery is not proof work resumed.

## On recovery

Read the checkpoint and current state first. Respect a user stop, cancellation,
or scope change. Check for an already-running/recovered worker and duplicate
continuations before starting work. Compare HEAD, dirty files, and the spec
revision with the checkpoint; investigate unexpected changes before writing.
Inspect partial edits and interrupted commands. Never discard work, repeat
non-idempotent external actions, or interpret an interrupted test as passed.

If native recovery continued the original task, consume its actual result via
the normal completion wake. Otherwise, once the old task and any recovery are
confirmed stopped, start one fresh `delegate_task` on the same provider/model
with the saved brief, checkpoint, and remaining work. Every review re-check is
a fresh delegate with its ledger; never use `t3_thread_send` to reopen a review.
Record its new task/request IDs. An interrupted attempt earns no approval and
does not reset the review-round budget. Re-run affected checks and require
approval of the final behavior diff before finishing.

## Verified boundaries

On 2026-10-28, this machine had `autoResumeLimitedThreads: true`. Check the
execution environment again on later runs; this is not a guarantee for other
machines. Native recovery needs a usable reset time and a running T3 server.
Unknown reset times, unavailable leader capacity, and offline hosts require the
explicit fallback or blocked outcome above.

- [T3 usage-limit recovery worker](https://github.com/pingdotgg/t3code/blob/main/apps/server/src/orchestration-v2/UsageLimitRecoveryWorker.ts)
- [Resume after usage limits](https://github.com/pingdotgg/t3code/blob/main/docs/user/thread-sidebar.md#inspect-agent-work)
- [Usage and subscription limits](https://github.com/pingdotgg/t3code/blob/main/docs/user/usage.md#track-subscription-limits)
