# copilot-toolkit

Reusable, generic building blocks for working with GitHub Copilot (CLI and IDE agent mode): specifications, skills, prompts and small standard-library tools.

Everything here is generic. Nothing in this repository describes a specific employer, project, system or person, and nothing should be added that does.

## Contents

| Kit | What it is | Start here |
|---|---|---|
| [brain-lite](brain-lite/) | **Start here.** Low-maintenance project brain: say "save: …" and ask questions; Copilot files plans, releases, bugs, tests, reports, modules, processes and meetings, logs and commits. One file to copy. | [brain-lite/README.md](brain-lite/README.md) |
| [second-brain](second-brain/) | A project-scoped Markdown "second brain" that Copilot maintains: meeting transcripts, plans, decisions, action items; rules, seven skills, templates, tests, a checker and a transcript importer | [second-brain/README.md](second-brain/README.md) |

## Using a kit on a machine without file transfer

1. Open the file on github.com (no sign-in needed for this public repository).
2. Click **Raw**, select all, copy.
3. Paste into a new file in your editor and save it with the same name and path.
4. Run the kit's self-check (each kit's README says which one). It shows whether the copy is complete.

## Rules for adding to this repository

- Generic only: no internal names, documents, code, screenshots or data from any workplace.
- Every kit has a README with its purpose, setup steps and a way to verify it works.
- Tools use only the standard library of their language, so they run without installing packages.
