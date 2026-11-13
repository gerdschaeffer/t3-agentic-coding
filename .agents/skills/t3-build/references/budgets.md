# Loop budgets

Limits per loop. Delegate launches and writers per run are in `SKILL.md` section 7.

| Loop | Budget | Stops early when | When the budget is used up |
|---|---|---|---|
| Spec review | 2 rounds (T2: 3) | Approved, or only nits left | Human decides |
| Make tests green | 3 attempts | Green | One model tier up, once. Opus goes straight to the lead |
| Code review | 3 rounds, up to 5 for security, data loss or concurrency | Approved, or only nits left | The pull request goes to the review lane as disputed, human decides |
| Security pass | Inside the code review budget | Both reviewers clean | An open finding blocks the merge. Only the human can accept a security risk |
| Consultation | 1 per open question | Answered | Lead decides and records why |
| Third voice | 1 per irreversible decision | | |
| OpenRouter spend | Soft ceiling per task (start: 3 USD), hard cap on the API key | | Paid work stops, rest is reported |

## Is another round worth it? Three questions

1. Is a finding of severity *blocker* or *should* still open, with evidence? If not, stop.
2. Did the last round reduce the open findings? If not, the loop is ping-pong. Escalate, do not
   repeat.
3. Is budget left? If not, stop and hand the open points to the human.

Severity has three levels only. **Blocker**: the intended case is wrong. **Should**: a real bug
that callers will hit. **Nit**: everything else. Nits never start a new round; they go to a
follow-up list. Security findings are the exception: at any severity they are fixed or go to
the human.
