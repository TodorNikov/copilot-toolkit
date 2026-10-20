# brain-lite

A low-maintenance "second brain" for **one project**, kept by GitHub Copilot. You say what to remember ("запиши: …" / "save: …") and ask questions; Copilot files everything into pages (plans, releases, bugs, tests, reports, modules, processes, meetings, decisions, glossary), links them, logs the change, runs a safety check and commits to Git.

| File | Purpose |
|---|---|
| [BRAIN-LITE.md](BRAIN-LITE.md) | The only file you need: short guide, setup prompt, and every file Copilot creates (rules, three skills, scripts). |
| [tools/brain_check.py](tools/brain_check.py) | Safety check (Python 3.8+, standard library). Compares with the last Git commit: sources never edited, log never rewritten, no broken links, no half-written files, no passwords. Identical to the copy inside BRAIN-LITE.md. |
| [tools/import_source.py](tools/import_source.py) | Turns Teams/Zoom transcripts (`.vtt`, `.docx`) and `.txt`/`.md` into a saved source. Identical to the copy inside BRAIN-LITE.md. |

## Setup

1. Make an empty folder next to your project, e.g. `C:\work\myproject-brain`.
2. Copy `BRAIN-LITE.md` into it (Raw → copy → paste → save).
3. Start `copilot` there and paste the **Setup prompt** from BRAIN-LITE.md with your project name and description.
4. Done when Copilot shows `selftest: 18/18 passed` and `import selftest: 11/11 passed`.

## Daily use

```text
запиши: release 2.4 moves to 12 Nov because bug LOGIN-12 is not fixed
запиши inbox\sync.vtt
какво знаем за release 2.4?
върни последното
прегледай мозъка
```

After 3–4 weeks: [EVALUATION.md](EVALUATION.md) is a one-page check (10 questions, with and without the brain) of whether the brain actually helps.

For more control and formal tests, see the full [second-brain](../second-brain/) kit.

## Status

lite-1.0. Built from BRAIN-LITE.md and checked offline: selftests pass; simulated saves (note, Teams transcript), undo with `git revert`, and broken saves were tested. Not yet run end-to-end with Copilot.
