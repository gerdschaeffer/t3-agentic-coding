# Event log

One JSON line per stage, appended to `<state>/<repo>/events.jsonl`. The monthly retro (WP8)
reads it next to git. Recording costs one call and no model.

```
echo '{"slice":"wp4","tier":"T2","stage":"code-review","role":"reviewer","model":"gpt-6.1-sol","effort":"medium","round":1,"verdict":"CHANGES","findings":{"blocker":0,"should":2,"nit":0,"security":0},"outcome":"ok","duration":412}' \
  | python3 <skill dir>/scripts/log_event.py --repo <repo>
```

`ts` is added by the script. Required: `slice`, `tier`, `stage`, `role`, `model`, `effort`.
Optional: `attempt`, `round`, `verdict`, `findings`, `outcome`, `duration` (seconds),
`tier_raised`, `escalation`, `quota_pause`. Rules per field: `scripts/log_event.py` and
`docs/specs/003-wp3-event-log/spec.md`.

- **Stages:** `tier`, `spec`, `spec-review`, `tests`, `implement`, `code-review`,
  `security-pass`, `rating`, `ship`, `human-gate`, `qa`, `release`. A T0 run logs `tier`,
  `implement`, `ship`.
- **When:** after each stage, each retry (`attempt`), each review round (`round`), each human
  gate (`outcome`: `ok`, `blocked`, `escalated`, `disputed`, `failed`, `skipped`).
- **Model and effort:** what the runtime confirmed; `default` when it confirmed none.
- **State dir:** `~/.local/state/t3-build`, unless `T3_BUILD_STATE` or `--state-dir` says
  otherwise. Where the home folder is not writable (sandbox), set one of them to a folder in the
  session scratchpad and say so in the report.
- **Exit codes:** 0 appended; 2 invalid event, nothing written, message on stderr: fix it and
  call again; 1 I/O or permission problem: report it, go on.
- **Never log** free text, prompts, paths or secrets. The script has no field for them.
