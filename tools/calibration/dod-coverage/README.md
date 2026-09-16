# dod-coverage-reviewer calibration (ADT-114 C1b)

**Why this exists.** `--gate` refuses a ticket whose DoD has not been
counter-checked by the `dod-coverage-reviewer` subagent. A reviewer that always
answers COVERED would make that gate **worse than no gate** — it would report the
coverage question settled when nothing checked it, which is mechanism 1's own
failure rebuilt one level up. So the reviewer is calibrated before it is trusted.

**Why it is not a pytest.** The reviewer is a model, and pytest has no model.
Testing the *gate logic* and calibrating the *judge* are different jobs, and
conflating them is how a rubber stamp gets mistaken for a gate. The gate logic is
`tools/tests/test_dod_coverage_gate.py`. This is the judge.

**The cases are real.** Each fixture is a ticket whose DoD carries a gap class
ADT actually shipped — not invented failures. Sources are
cited per case. Two cases are deliberately CLEAN: a judge that flags everything is
as useless as one that flags nothing, and false-positive rate is part of the score.

## Running it

```bash
python3 tools/calibration/dod-coverage/score.py --help
```

1. For each `cases/*.md`, hand the file to the `dod-coverage-reviewer` subagent
   (a real subagent — a separate context window is the whole point; grading the
   cases yourself in the authoring session reproduces the failure being measured).
2. Save each verdict block to `cases/<name>.verdict`.
3. Run `score.py` to compare verdicts against `expected.json` and write
   `docs/dod-coverage-calibration.md`.

## Reading the score

`caught: <k>/<n>` over the gapped cases, plus the false-positive count over the
clean ones. **A poor score is a legitimate result, not a failure of this
exercise** — the correct response is to stop gating on the judge (relax C3 to
recording the review without refusing), never to re-run until it looks good.
