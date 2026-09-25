# A non-zero exit means not done

A command exits 0 when it did what it says, and non-zero when it did not. The
error on stderr says why, under `--json` mostly as an object. The number
itself carries no meaning: some errors end with 1, some with 2, and no
document promises which.

Until this decision the documentation made three promises at once. The README
said there was deliberately no second exit code. AGENTS.md and the agent skill
said 1 meant a failure and 2 a refused call, and told agents to rely on it.
The contributing guide and a test said a refusal exits 1. The code kept none of
them fully: most refused calls exited 1, a few exited 2, and the same mistake
could get either, depending on which check caught it.

## Considered Options

**Three codes, derived from the kind of error: 0 done, 1 failed, 2 refused.**
A caller could tell "change the call" from "something outside the call went
wrong" without reading the error. Worked out in full, with every error path of
every command classified, and deferred. Nothing showed that a caller needs it.
In a month of agent sessions with this tool, agents read the error text and
corrected their calls. Most calls ran through a pipe without `pipefail`, so
the exit status did not reach them at all. No script in this repository tells
1 from 2. The measurement cannot show that agents would not use the
distinction if it existed, since it never did reliably. It shows that nobody
is waiting for it, and the change touches every command.

**One code for every error, with 2 reserved for the command line parser.**
The README's old position. It would have made AGENTS.md and the skill true by
taking the promise back, but it also keeps a rule about the number that the
code has to keep, and two input checks already use 2 on purpose.

**Correct the documentation to list which error exits with which code.** A
list maintained by hand, next to the code it describes, drifts. That drift is
how the three promises came about.

## Decision

The number carries no meaning, and the documentation says so. Nothing needs
to keep a mapping, so nothing can contradict one. `tests/test_exit_status_rule.py`
checks that no document or help text gives 1 or 2 a meaning, and that no new
error sets its exit code by hand.

The rule demands one thing of the code: `0` is never a lie. A call that exits
0 without having done what it says is a bug, whatever it printed. Issue #93
collected the ones found when this was decided.

## Consequences

- Callers check for zero or non-zero and read the error for the reason.
- Because nobody relies on 1 versus 2, the numbers can gain a meaning later
  without breaking a caller.
- Revisit when a script or an agent in actual use needs to tell a refused call
  from a failed one, for example a scheduled job that should retry while
  Logseq is not running but report a call that is wrong.
