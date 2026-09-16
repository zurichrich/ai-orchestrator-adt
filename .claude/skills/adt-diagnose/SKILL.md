---
name: adt-diagnose
description: Force a verified mechanism before any fix — point to the exact line producing the wrong value and the value at each step. Realises working-style.md change #1.
disable-model-invocation: true
---

# diagnose — verify the mechanism before proposing a fix

Mechanical enforcement of `.claude/rules/working-style.md` change #1:
no fix is proposed until the mechanism is shown.

## Diagnosis is a loop, not a guess

Diagnosis is inherently iterative: you hold a hypothesis about which line
produces the wrong value, test it against the actual value at that line, and
**narrow** — refute it and form the next, or confirm it and stop. Run that
loop until exactly one mechanism survives with evidence. This is an
**artifact-bounded loop**: the exit condition is *a cited value at a specific
line*, never "I'm now fairly sure" (working-style #1). A fix proposed before
the loop converges is a guess wearing a diagnosis's clothes.

Each round:

1. **Hypothesis** — "the wrong value is produced at `file:line` because <X>."
   State it as a falsifiable claim, not a vibe.
2. **Test it** — read the line; trace the actual value flowing into and out of
   it (a print, a `python -c`, a unit-test probe, or the forensic subagent for
   history). Get the *real number/state*, not an assumed one.
3. **Narrow:**
   - **Refuted** (the value there is correct) → the cause is upstream or
     elsewhere; form the next hypothesis from where the value was still right.
     Loop.
   - **Confirmed** (the wrong value is demonstrably born at this line) → the
     loop converges. Go to the verdict.
4. **Bound** — if ~3–4 rounds don't isolate a single line, you're missing
   instrumentation or context: say "still diagnosing — mechanism not yet
   shown," name what you'd need to see next, and **stop**. Do not propose a fix
   to buy progress, and do not emit a menu of maybe-causes (working-style #4).

When the loop converges, produce the record below.

Produce, before any fix is allowed:

1. **The wrong value observed** — what is actually happening, with the
   concrete number / state (not "it seems off").
2. **The exact line** producing it — `file:line`, the code quoted.
3. **The value at each step** — trace input → that line → output,
   showing how the wrong value is computed. When the mechanism spans the
   project's *history* (logs, data tables, git log/diff, past incidents) rather
   than a single live line, escalate to the **`adt-forensic-investigator`**
   subagent (the ADT default; a project may have its own instance) — it's the
   read-only "why did X happen" investigator that returns a timeline + causal
   chain with `[verified]`/`[speculation]` tags:
   ```
   Agent({subagent_type: "adt-forensic-investigator",
          description: "Trace the mechanism",
          prompt: "Why did <X> happen? Trace from <symptom> to the
                   exact line. Tag [verified] vs [speculation]."})
   ```
4. **The verdict line:** either
   - "Cause verified: <mechanism> at file:line" → now a fix may be
     proposed, and it must say whether it fixes the cause or works
     around it (working-style #3), or
   - "Still diagnosing — mechanism not yet shown" → say only that.
     No fix, no menu of maybe-causes.

If you can't show the value at each step, you are still diagnosing.

<!-- adt-bundle: v0.1.0 -->
