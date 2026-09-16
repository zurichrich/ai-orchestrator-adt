# plan-quality-reviewer calibration (ADT-126)

**Why this exists.** `adt_dod.py --gate` can refuse a ticket whose Design was
counter-checked and came back `FLAWED`. A reviewer that always answers `SOUND`
would make that gate **worse than no gate** — it would report the design question
settled when nothing checked it, which is the failure ADT-114 built the coverage
reviewer to close, rebuilt one level up. So the reviewer is calibrated before it
is trusted, and the gate is conditional on the result.

**Why it is not a pytest.** The reviewer is a model, and pytest has no model.
Testing the *gate logic* and calibrating the *judge* are different jobs, and
conflating them is how a rubber stamp gets mistaken for a gate. The gate logic is
`tools/tests/test_plan_quality_gate.py`. This is the judge.

**The cases are real.** Each fixture is a plan whose design carried a defect ADT
actually shipped or nearly shipped, drawn from `done/` tickets and
ADT-114's own build. Sources are cited per case in `expected.json`. Two cases are
deliberately SOUND: a judge that flags everything is as useless as one that flags
nothing, and false-positive rate is half the score.

## THE BAR — declared here, before any verdict exists

Gating is enabled only if **both** hold, measured on the **held-out** subset:

    held-out caught >= 2 of 3 held-out gapped cases
    false positives <= 1 of 2 clean cases

**Why held-out.** The first six flawed cases are drawn from the same *instances*
the reviewer's own prompt cites as worked examples (ADT-101/109/114/116/126). A
score on those measures recall on material the judge was handed: it is
near-guaranteed to clear any bar and says nothing about generalisation.
Declaring the bar before scoring stops goalpost-moving; it does nothing about a
contaminated eval set. So those six are still scored and reported — as a **recall
floor** — but they cannot buy gating. The three `held-*` cases use instances the
prompt never mentions (ADT-054, ADT-071/072, ADT-090); classes may overlap,
instances must not, and only those decide `gating:`.

This split was not in the plan. It was found by running the
`plan-quality-reviewer` against ADT-126 — the ticket that builds it — which
returned FLAWED and named the contamination as "a check that cannot fail".

`score.py` writes `gating: ENABLED` or `gating: DISABLED` into
`docs/plan-quality-calibration.md`, and `adt_dod.py` reads that line. Below the
bar the reviewer still runs and its verdict is still recorded — only the
automatic refusal is withheld. **A poor score is a legitimate result**, and the
correct response is to leave gating disabled, never to re-run until it looks
good. This file is committed in the sub-step that creates the cases,
before the first `.verdict` exists, so the bar cannot be fitted to the score.

## Running it

```bash
python3 tools/calibration/plan-quality/score.py --help
```

1. For each `cases/*.md`, hand the file to the `plan-quality-reviewer` subagent
   (a real subagent — a separate context window is the whole point; grading the
   cases yourself in the authoring session reproduces the failure being measured).
2. Save each verdict block to `cases/<name>.verdict`.
3. Run `score.py` to compare verdicts against `expected.json` and write
   `docs/plan-quality-calibration.md`.

`score.py --require-complete` exits non-zero until every case has a verdict; it
does **not** fail on a poor score, by design.
