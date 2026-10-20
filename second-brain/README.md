# second-brain

A Markdown "second brain" for **one project**, maintained by GitHub Copilot. It collects meeting transcripts, meeting notes, plans, decisions, action items and your own notes, and keeps them as linked, sourced pages. It follows the "LLM wiki" pattern: knowledge is compiled once into maintained pages, not re-derived on every question.

| File | Purpose |
|---|---|
| [BUILD-SPEC.md](BUILD-SPEC.md) | Complete build specification: phases, rules (`AGENTS.md`), seven skills, templates, tests T1–T17. Contains every file to create, verbatim. |
| [tools/brain_check.py](tools/brain_check.py) | Checker (Python 3.8+, standard library only): `check`, `snapshot`, `diff`, `selftest`. |
| [tools/import_source.py](tools/import_source.py) | Importer for Teams/Zoom transcripts (`.vtt`, `.docx`) and `.txt`/`.md` into `raw/`: removes timestamps, merges speaker lines, redacts credentials, never overwrites. |

Both scripts are identical to the copies embedded in BUILD-SPEC.md section 9; they are kept separate so they are easier to copy.

## Quick start

1. Create an empty folder for the brain and run `git init` in it.
2. Copy `BUILD-SPEC.md` into it (open it here, click **Raw**, copy all, paste, save).
3. Copy both scripts into `tools/` the same way.
4. Check the copies: `python tools/brain_check.py selftest` → `27/27 passed`; `python tools/import_source.py selftest` → `11/11 passed`.
5. Start `copilot` in the folder and give it the Phase 1 prompt from BUILD-SPEC.md section 2, filled with your project name, slug, goal and scope.
6. After Phase 1: `python tools/brain_check.py check` must end with `-> PASS`.
7. Run the Phase 2 tests (section 10) in a separate test brain before real use.

## After a meeting

```text
python tools/brain_check.py snapshot meta/snapshots/last.json
python tools/import_source.py inbox/<transcript>.vtt --date YYYY-MM-DD --title "<meeting title>"
(in copilot) /brain-meeting raw/<created file>.md
python tools/brain_check.py diff meta/snapshots/last.json
python tools/brain_check.py check
git add -A
git commit -m "meeting: <title>"
```

On FAIL, roll back to the last commit with `git restore .` and `git clean -fd` (preview with `git clean -nd`).

## Status

Version 1.1. A brain built from the spec passes `check` and both selftests. Simulated correct runs (transcript import, meeting processing, closing an action) pass. Simulated broken runs (edited source, rewritten log, deleted action row, invalid status, corrupted Cyrillic, truncated file) are detected. Not yet run end-to-end with Copilot; Phase 0 and Phase 2 of the spec cover that.
