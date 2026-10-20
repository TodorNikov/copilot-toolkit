# second-brain

A Markdown knowledge base that GitHub Copilot reads before answering and updates after learning something. It follows the "LLM wiki" pattern: knowledge is compiled once into maintained pages, not re-derived on every question.

| File | Purpose |
|---|---|
| [BUILD-SPEC.md](BUILD-SPEC.md) | Complete build specification: phases, rules (`AGENTS.md`), five skills, templates, tests T1–T12. Contains every file to create, verbatim. |
| [tools/brain_check.py](tools/brain_check.py) | Checker (Python 3.8+, standard library only): `check`, `snapshot`, `diff`, `selftest`. Identical to the copy embedded in BUILD-SPEC.md section 9; kept separate so it is easier to copy. |

## Quick start

1. Create an empty folder for the brain and run `git init` in it (optional but recommended).
2. Copy `BUILD-SPEC.md` into it (open it here, click **Raw**, copy all, paste, save).
3. Copy `tools/brain_check.py` into `tools/` the same way.
4. Check the copy: `python tools/brain_check.py selftest` must print `selftest: 20/20 passed`.
5. Start `copilot` in the folder and give it the Phase 1 prompt from BUILD-SPEC.md section 2.
6. After Phase 1: `python tools/brain_check.py check` must end with `-> PASS`.
7. Run the Phase 2 tests (section 10) in a copy of the brain before real use.

## Safe write loop

```text
python tools/brain_check.py snapshot meta/snapshots/last.json
(run a brain-* skill in Copilot)
python tools/brain_check.py diff meta/snapshots/last.json
python tools/brain_check.py check
git add -A
git commit -m "ingest: <what>"
```

On FAIL, roll back to the last commit with `git restore .` and `git clean -fd` (preview with `git clean -nd`).

## Status

Version 1.0. The scaffold built from the spec passes `check` and `selftest`. A simulated correct ingest passes and a simulated broken ingest is detected. Not yet run end-to-end with Copilot; Phase 0 and Phase 2 of the spec cover that.
