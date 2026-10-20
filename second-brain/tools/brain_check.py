"""brain_check.py - structural and write-integrity checks for the Copilot second brain.

Standard library only (Python 3.8+). Read-only, except `snapshot`, which writes the JSON file you name.
It finds format and integrity defects. It does NOT prove that the knowledge is true.

Usage (run from the brain root):
  python tools/brain_check.py check                 static checks of all notes
  python tools/brain_check.py snapshot FILE.json    save hashes + log sections (before a write session)
  python tools/brain_check.py diff FILE.json        compare the current state with a saved snapshot
  python tools/brain_check.py selftest              verify that this script itself works
Options: --root PATH (default: parent of tools/), --today YYYY-MM-DD
Exit code: 0 = no ERROR, 1 = at least one ERROR, 2 = usage problem.
"""
import datetime as dt
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

VERSION = "1.1"

FOLDER_TYPES = {
    "raw": "source",
    "wiki/projects": "project",
    "wiki/decisions": "decision",
    "wiki/concepts": "concept",
    "wiki/glossary": "term",
    "wiki/agents": "agent",
    "wiki/people": "role",
    "wiki/howto": "howto",
    "wiki/questions": "question",
    "wiki/meetings": "meeting",
    "wiki/plans": "plan",
}
ROOT_PAGES = {"wiki/index.md": "index", "wiki/log.md": "log", "wiki/overview.md": "overview",
              "wiki/actions.md": "actions"}

KNOWLEDGE_STATUS = {"unreviewed", "partially_verified", "verified", "disputed", "superseded"}
STATUS_BY_TYPE = {
    "source": {"captured"},
    "decision": {"proposed", "accepted", "rejected", "superseded"},
    "question": {"open", "answered", "dropped"},
    "plan": {"draft", "active", "done", "dropped", "superseded"},
}
COMMON_FIELDS = ["type", "title", "tags", "status", "created", "updated"]
REQUIRED_BY_TYPE = {
    "source": ["type", "title", "tags", "status", "captured", "origin"],
    "index": ["type", "title", "tags"],
    "log": ["type", "title", "tags"],
    "overview": ["type", "title", "tags", "updated"],
    "actions": ["type", "title", "tags", "updated"],
    "meeting": COMMON_FIELDS + ["held"],
}
SCOPE_REQUIRED = {"project", "decision", "agent", "howto", "question", "meeting", "plan"}
INDEX_REQUIRED = {"project", "decision", "agent", "howto", "question", "plan"}
ORIGINS = {"meeting", "chat", "doc-excerpt", "own-note", "web", "code-review", "other"}
DATE_FIELDS = ["created", "updated", "captured", "held", "verified_at", "review_after"]

REQUIRED_FILES = [
    "AGENTS.md",
    ".github/copilot-instructions.md",
    ".github/skills/brain-ingest/SKILL.md",
    ".github/skills/brain-query/SKILL.md",
    ".github/skills/brain-correct/SKILL.md",
    ".github/skills/brain-decision/SKILL.md",
    ".github/skills/brain-lint/SKILL.md",
    ".github/skills/brain-meeting/SKILL.md",
    ".github/skills/brain-status/SKILL.md",
    "wiki/index.md",
    "wiki/log.md",
    "wiki/overview.md",
    "wiki/actions.md",
    "templates/source.md",
    "templates/page.md",
    "templates/decision.md",
    "templates/agent.md",
    "templates/meeting.md",
    "templates/plan.md",
    "meta/regression-cases.md",
    "tools/brain_check.py",
    "tools/import_source.py",
]

STEM_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RAW_STEM_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-[a-z0-9]+(?:-[a-z0-9]+)*$")
ADR_STEM_RE = re.compile(r"^adr-\d{4}-[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ACTION_ROW_RE = re.compile(r"^\|\s*(A-\d{3,})\s*\|(.*)\|$")
ACTION_STATUS = {"open", "done", "dropped"}
PROJECT_SLUG_RE = re.compile(r"(?m)^Project slug:\s*`?([^`\s]+)`?\s*$")
LINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")
LOG_HEAD_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}) (?:\u2014|-) \S.*$")

PLACEHOLDER_RES = [
    re.compile(r"\{\{[^}\n]+\}\}"),
    re.compile(r"(?i)\brest of (?:the )?(?:file|note|content|document|page)\b"),
    re.compile(r"(?i)\b(?:existing|previous|original) content (?:remains|unchanged|here|goes here)\b"),
    re.compile(r"(?i)\.\.\.\s*\((?:truncated|omitted|unchanged)\)"),
    re.compile(r"(?i)\blorem ipsum\b"),
]
MOJIBAKE_RE = re.compile(
    "[\u00d0\u00d1][\u0080-\u00bf\u2018-\u203a\u20ac\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u0192\u02c6\u02dc]"
    "|\u00e2\u20ac"
    "|\u0420[\u00b0\u00b1\u00b5\u00b6\u00b7\u0406\u0456\u0491\u0451\u2116\u0454\u0458\u0405\u0455\u0457]"
    "|\u0421[\u0402\u0403\u201a\u0453\u201e\u2020\u2021\u20ac\u2030\u0409\u2039\u040a\u040c\u040b\u040f]"
)
SECRET_RES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bAccountKey=[A-Za-z0-9+/=]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]
POSSIBLE_SECRET_RE = re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*(?!\[REDACTED\])\S{6,}")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, code, where, msg):
        self.items.append((level, code, where, msg))

    def codes(self):
        return {code for _, code, _, _ in self.items}

    def errors(self):
        return [i for i in self.items if i[0] == "ERROR"]

    def print(self):
        order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        for level, code, where, msg in sorted(self.items, key=lambda i: (order[i[0]], i[2], i[1])):
            print(f"{level:5} {code:24} {where} - {msg}")
        e = len(self.errors())
        w = sum(1 for i in self.items if i[0] == "WARN")
        print(f"brain_check {VERSION}: {e} error(s), {w} warning(s) -> {'FAIL' if e else 'PASS'}")


def luhn_ok(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def parse_date(value):
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def split_frontmatter(text):
    """Return (fields, body, body_start_line, error). fields is None when there is no frontmatter."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text, 1, None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, text, 1, "frontmatter opened with --- but never closed"
    fields, err, current = {}, None, None
    for n, raw in enumerate(lines[1:end], 2):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        item = re.match(r"^\s+-\s+(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if key in fields:
                err = err or f"line {n}: duplicate key '{key}'"
            if value == "":
                fields[key] = []
                current = key
            elif value.startswith("["):
                if not value.endswith("]"):
                    err = err or f"line {n}: list for '{key}' is not closed with ]"
                    fields[key] = []
                else:
                    inner = value[1:-1].strip()
                    fields[key] = [v.strip().strip("\"'") for v in inner.split(",")] if inner else []
                current = None
            else:
                v = value.strip("\"'")
                fields[key] = None if v in ("null", "~") else v
                current = None
        elif item and current is not None and isinstance(fields.get(current), list):
            fields[current].append(item.group(1).strip().strip("\"'"))
        else:
            err = err or f"line {n}: cannot parse '{line.strip()[:40]}'"
    body = "\n".join(lines[end + 1:])
    return fields, body, end + 2, err


def strip_code(lines):
    """Yield (line_number, text) with fenced and inline code removed."""
    fence = None
    for n, line in lines:
        m = re.match(r"^\s*(```|~~~)", line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif fence == m.group(1):
                fence = None
            continue
        if fence:
            continue
        yield n, re.sub(r"`[^`]*`", "", line)


def note_location(rel):
    if rel in ROOT_PAGES:
        return ROOT_PAGES[rel]
    folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    return FOLDER_TYPES.get(folder)


def collect(root):
    notes = {}
    for folder in ["raw", "wiki"]:
        base = root / folder
        if base.is_dir():
            for p in sorted(base.rglob("*.md")):
                notes[p.relative_to(root).as_posix()] = p
    return notes


def check(root, today, report=None):
    r = report or Report()
    root = Path(root)
    for req in REQUIRED_FILES:
        if not (root / req).is_file():
            r.add("ERROR", "E_MISSING_REQUIRED", req, "required file is missing")
    # Encoding checks apply to every Markdown file in the brain.
    all_md = [p for p in root.rglob("*.md") if ".git" not in p.parts]
    texts = {}
    for p in all_md:
        rel = p.relative_to(root).as_posix()
        data = p.read_bytes()
        if data.startswith(b"\xef\xbb\xbf"):
            r.add("WARN", "W_BOM", rel, "UTF-8 BOM present; save as UTF-8 without BOM")
            data = data[3:]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            r.add("ERROR", "E_ENCODING", rel, f"not valid UTF-8 ({exc.reason} at byte {exc.start})")
            continue
        if "\r\n" in text and re.search(r"(?<!\r)\n", text):
            r.add("WARN", "W_MIXED_EOL", rel, "mixed CRLF and LF line endings")
        text = text.replace("\r\n", "\n")
        if "\ufffd" in text:
            r.add("ERROR", "E_ENCODING", rel, "contains U+FFFD replacement characters (lost characters)")
        m = MOJIBAKE_RE.search(text)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            r.add("ERROR", "E_MOJIBAKE", f"{rel}:{line}", f"looks like double-encoded text near '{m.group(0)}'")
        texts[rel] = text
    skills = root / ".github" / "skills"
    if skills.is_dir():
        for sk in sorted(skills.iterdir()):
            f = sk / "SKILL.md"
            rel = f.relative_to(root).as_posix()
            if not f.is_file() or rel not in texts:
                continue
            fields, _, _, err = split_frontmatter(texts[rel])
            if fields is None or err:
                r.add("ERROR", "E_SKILL_META", rel, err or "SKILL.md has no frontmatter")
            elif fields.get("name") != sk.name or not fields.get("description"):
                r.add("ERROR", "E_SKILL_META", rel, "name must equal the folder name and description must be set")
    project = None
    for rel in ("AGENTS.md", ".github/copilot-instructions.md"):
        if rel in texts:
            for n, line in strip_code(enumerate(texts[rel].split("\n"), 1)):
                if PLACEHOLDER_RES[0].search(line):
                    r.add("ERROR", "E_PLACEHOLDER", f"{rel}:{n}", "unfilled {{placeholder}} - fill in the project values")
    if "AGENTS.md" in texts:
        m = PROJECT_SLUG_RE.search(texts["AGENTS.md"])
        if m and STEM_RE.match(m.group(1)):
            project = m.group(1)
        else:
            r.add("ERROR", "E_NO_PROJECT", "AGENTS.md", "needs a line 'Project slug: <lowercase-kebab-slug>'")

    notes = collect(root)
    stems = {}
    for rel in notes:
        stems.setdefault(Path(rel).stem, []).append(rel)
    for stem, rels in stems.items():
        if len(rels) > 1:
            r.add("ERROR", "E_DUPLICATE_STEM", ", ".join(rels), f"[[{stem}]] would be ambiguous")

    parsed, inbound, cited = {}, {}, set()
    for rel in notes:
        if rel not in texts:
            continue
        fields, body, start, err = split_frontmatter(texts[rel])
        stem = Path(rel).stem
        expected = note_location(rel)
        if expected is None:
            r.add("ERROR", "E_UNKNOWN_LOCATION", rel, "file is not in a known folder for its type")
        if fields is None:
            r.add("ERROR", "E_NO_FRONTMATTER", rel, err or "missing YAML frontmatter block")
            fields = {}
        elif err:
            r.add("ERROR", "E_FRONTMATTER_PARSE", rel, err)
        parsed[rel] = (fields, body, start)
        ntype = fields.get("type")
        if expected and ntype != expected:
            r.add("ERROR", "E_TYPE_MISMATCH", rel, f"type is '{ntype}', folder requires '{expected}'")
        ntype = expected or ntype
        if rel not in ROOT_PAGES:
            if not STEM_RE.match(stem):
                r.add("ERROR", "E_BAD_FILENAME", rel, "file name must be lowercase-kebab-case ASCII")
            if ntype == "source":
                m = RAW_STEM_RE.match(stem)
                if not m:
                    r.add("ERROR", "E_BAD_FILENAME", rel, "raw file name must be YYYY-MM-DD-slug.md")
                elif fields.get("captured") and fields.get("captured") != m.group(1):
                    r.add("WARN", "W_DATE_MISMATCH", rel, "file name date differs from captured")
            if ntype == "decision" and not ADR_STEM_RE.match(stem):
                r.add("ERROR", "E_BAD_FILENAME", rel, "decision file name must be adr-NNNN-slug.md")
            if ntype == "meeting":
                m = RAW_STEM_RE.match(stem)
                if not m:
                    r.add("ERROR", "E_BAD_FILENAME", rel, "meeting file name must be YYYY-MM-DD-slug.md")
                elif fields.get("held") and fields.get("held") != m.group(1):
                    r.add("WARN", "W_DATE_MISMATCH", rel, "file name date differs from held")
        required = REQUIRED_BY_TYPE.get(ntype, COMMON_FIELDS)
        for key in required:
            if fields.get(key) in (None, "", []):
                r.add("ERROR", "E_MISSING_FIELD", rel, f"required field '{key}' is missing or empty")
        if ntype in SCOPE_REQUIRED and not fields.get("scope"):
            r.add("ERROR", "E_MISSING_FIELD", rel, "required field 'scope' is missing or empty")
        scope = fields.get("scope")
        if project and isinstance(scope, str) and scope and scope not in (project, "general"):
            r.add("WARN", "W_SCOPE_OTHER", rel, f"scope '{scope}' is neither this brain's project '{project}' nor 'general'")
        if "tags" in fields and not isinstance(fields["tags"], list):
            r.add("ERROR", "E_FRONTMATTER_PARSE", rel, "tags must be a list, e.g. tags: [project]")
        status = fields.get("status")
        if ntype not in ROOT_PAGES.values() and status is not None:
            allowed = STATUS_BY_TYPE.get(ntype, KNOWLEDGE_STATUS)
            if status not in allowed:
                r.add("ERROR", "E_BAD_STATUS", rel, f"status '{status}' not in {sorted(allowed)}")
        if ntype == "source" and fields.get("origin") and fields["origin"] not in ORIGINS:
            r.add("ERROR", "E_BAD_FIELD", rel, f"origin must be one of {sorted(ORIGINS)}")
        dates = {}
        for key in DATE_FIELDS:
            if fields.get(key) not in (None, "", []):
                d = parse_date(fields[key])
                if d is None:
                    r.add("ERROR", "E_BAD_DATE", rel, f"{key} must be YYYY-MM-DD, got '{fields[key]}'")
                else:
                    dates[key] = d
                    if key != "review_after" and d > today:
                        r.add("WARN", "W_FUTURE_DATE", rel, f"{key} {d} is after today {today}")
        if "created" in dates and "updated" in dates and dates["updated"] < dates["created"]:
            r.add("ERROR", "E_BAD_DATE", rel, "updated is earlier than created")
        if "review_after" in dates and dates["review_after"] < today:
            r.add("WARN", "W_REVIEW_DUE", rel, f"review_after {dates['review_after']} has passed")
        sources = fields.get("sources") or []
        if not isinstance(sources, list):
            r.add("ERROR", "E_FRONTMATTER_PARSE", rel, "sources must be a list")
            sources = []
        if status == "verified" and (not sources or "verified_at" not in dates):
            r.add("ERROR", "E_VERIFIED_NO_EVIDENCE", rel, "status verified needs non-empty sources and verified_at")
        if status == "superseded":
            target = fields.get("superseded_by")
            if not target or target not in stems:
                r.add("ERROR", "E_SUPERSEDED_TARGET", rel, "superseded needs superseded_by: <existing-note-stem>")
        for s in sources:
            s = s.strip("[]")
            if s.startswith("ext:"):
                continue
            if s not in stems:
                r.add("ERROR", "E_BAD_SOURCE", rel, f"source '{s}' is not an existing note stem (or ext:...)")
            else:
                cited.add(s)
        if ntype != "source" and ntype not in ("index", "log") and len(body.strip()) < 20:
            r.add("ERROR", "E_EMPTY_BODY", rel, "note body is empty or almost empty")
        body_lines = list(enumerate(body.split("\n"), start))
        for n, line in strip_code(body_lines):
            for m in LINK_RE.finditer(line):
                target = m.group(1).split("|", 1)[0].split("#", 1)[0].strip()
                if target.endswith(".md"):
                    target = target[:-3]
                if "/" in target or "\\" in target:
                    r.add("ERROR", "E_LINK_HAS_PATH", f"{rel}:{n}", f"[[{target}]] - use the bare file name")
                    continue
                if target not in stems:
                    r.add("ERROR", "E_BROKEN_LINK", f"{rel}:{n}", f"[[{target}]] does not resolve")
                else:
                    inbound.setdefault(target, set()).add(rel)
            for pat in PLACEHOLDER_RES:
                if pat.search(line):
                    r.add("ERROR", "E_PLACEHOLDER", f"{rel}:{n}", "template placeholder or truncation marker left in note")
                    break
            if re.search(r"\bTODO\b", line):
                r.add("WARN", "W_TODO", f"{rel}:{n}", "TODO left in note")
        for n, line in body_lines:
            for pat in SECRET_RES:
                if pat.search(line):
                    r.add("ERROR", "E_SECRET", f"{rel}:{n}", "looks like a credential or key - remove it")
                    break
            else:
                if POSSIBLE_SECRET_RE.search(line):
                    r.add("WARN", "W_POSSIBLE_SECRET", f"{rel}:{n}", "possible credential - check manually")
            if IBAN_RE.search(line):
                r.add("WARN", "W_POSSIBLE_PII", f"{rel}:{n}", "looks like an IBAN - no customer data in the brain")
            for m in CARD_RE.finditer(line):
                digits = re.sub(r"\D", "", m.group(0))
                if 13 <= len(digits) <= 19 and luhn_ok(digits):
                    r.add("WARN", "W_POSSIBLE_PII", f"{rel}:{n}", "looks like a card number - no customer data in the brain")
                    break

    index_rel = "wiki/index.md"
    index_links = set()
    if index_rel in parsed:
        _, body, start = parsed[index_rel]
        for _, line in strip_code(list(enumerate(body.split("\n"), start))):
            index_links.update(m.group(1).split("|", 1)[0].split("#", 1)[0].strip() for m in LINK_RE.finditer(line))
    for rel, (fields, _, _) in parsed.items():
        stem = Path(rel).stem
        ntype = note_location(rel)
        if rel in ROOT_PAGES:
            continue
        if ntype in INDEX_REQUIRED and stem not in index_links:
            r.add("WARN", "W_NOT_IN_INDEX", rel, "not linked from wiki/index.md")
        if ntype == "source":
            if stem not in cited and stem not in inbound:
                r.add("WARN", "W_UNINGESTED", rel, "raw source is not cited or linked by any wiki page")
        elif stem not in inbound:
            r.add("WARN", "W_ORPHAN", rel, "no other note links here")

    log_rel = "wiki/log.md"
    if log_rel in parsed:
        _, body, start = parsed[log_rel]
        last = None
        for n, line in strip_code(list(enumerate(body.split("\n"), start))):
            if line.startswith("## "):
                m = LOG_HEAD_RE.match(line)
                d = parse_date(m.group(1)) if m else None
                if not d:
                    r.add("ERROR", "E_LOG_HEADING", f"{log_rel}:{n}", "log headings must be '## YYYY-MM-DD - title'")
                    continue
                if last and d > last:
                    r.add("ERROR", "E_LOG_ORDER", f"{log_rel}:{n}", "newest entries must be on top")
                last = d

    actions_rel = "wiki/actions.md"
    if actions_rel in texts:
        seen = set()
        for n, line in enumerate(texts[actions_rel].split("\n"), 1):
            m = ACTION_ROW_RE.match(line.strip())
            if not m:
                continue
            aid, cells = m.group(1), [c.strip() for c in m.group(2).split("|")]
            if aid in seen:
                r.add("ERROR", "E_ACTION_DUP", f"{actions_rel}:{n}", f"action id {aid} is used twice")
            seen.add(aid)
            if len(cells) != 5:
                r.add("ERROR", "E_ACTION_ROW", f"{actions_rel}:{n}",
                      "row must be | id | action | owner | due | source | status |")
            elif cells[4] not in ACTION_STATUS:
                r.add("ERROR", "E_BAD_STATUS", f"{actions_rel}:{n}", f"action status '{cells[4]}' not in {sorted(ACTION_STATUS)}")
    return r


def action_ids(text):
    ids = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = ACTION_ROW_RE.match(line.strip())
        if m:
            ids.append(m.group(1))
    return ids


def log_sections(text):
    text = text.replace("\r\n", "\n")
    parts = re.split(r"(?m)^(?=## )", text)
    return [p.strip() for p in parts if p.startswith("## ")]


def snapshot(root):
    root = Path(root)
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.parts and "snapshots" not in p.parts:
            files[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    log = root / "wiki" / "log.md"
    sections = log_sections(log.read_bytes().decode("utf-8", "replace")) if log.is_file() else []
    actions = root / "wiki" / "actions.md"
    ids = action_ids(actions.read_bytes().decode("utf-8", "replace")) if actions.is_file() else []
    return {"version": VERSION, "taken": dt.datetime.now().isoformat(timespec="seconds"),
            "files": files, "log_sections": sections, "action_ids": ids}


def diff(root, old, report=None):
    r = report or Report()
    new = snapshot(root)
    of, nf = old["files"], new["files"]
    for rel, h in sorted(of.items()):
        if rel not in nf:
            if rel.startswith("raw/"):
                r.add("ERROR", "E_RAW_DELETED", rel, "raw sources are immutable and must not be deleted")
            else:
                r.add("WARN", "W_DELETED", rel, "file was deleted - confirm this was intended")
        elif nf[rel] != h:
            if rel.startswith("raw/"):
                r.add("ERROR", "E_RAW_MODIFIED", rel, "raw sources are immutable and must not be edited")
            else:
                r.add("INFO", "I_MODIFIED", rel, "modified")
    for rel in sorted(set(nf) - set(of)):
        r.add("INFO", "I_ADDED", rel, "added")
    for aid in old.get("action_ids", []):
        if aid not in new["action_ids"]:
            r.add("ERROR", "E_ACTION_REMOVED", "wiki/actions.md", f"action {aid} was removed; set its status instead")
    current = set(new["log_sections"])
    for sec in old["log_sections"]:
        if sec not in current:
            r.add("ERROR", "E_LOG_REWRITTEN", "wiki/log.md", f"old entry changed or removed: '{sec.splitlines()[0][:60]}'")
    changed = [x for x in r.items if x[1] in ("I_MODIFIED", "I_ADDED") and x[2] != "wiki/log.md"]
    if changed and len(new["log_sections"]) <= len(old["log_sections"]):
        r.add("WARN", "W_NO_LOG_ENTRY", "wiki/log.md", "files changed but no new log entry was added")
    return r


def selftest():
    fm = lambda **kv: "---\n" + "".join(f"{k}: {v}\n" for k, v in kv.items()) + "---\n"
    today = dt.date(2026, 10, 20)
    good = {
        "AGENTS.md": "# Rules\n\nProject slug: compliance-agent\nNever leave `{{placeholders}}` in notes.\n",
        ".github/copilot-instructions.md": "Follow AGENTS.md\n",
        "tools/brain_check.py": "# copy\n", "tools/import_source.py": "# copy\n",
        "templates/source.md": "x\n", "templates/page.md": "x\n",
        "templates/decision.md": "x\n", "templates/agent.md": "x\n",
        "templates/meeting.md": "x\n", "templates/plan.md": "x\n",
        "wiki/actions.md": fm(type="actions", title="Actions", tags="[actions]", updated="2026-10-20")
        + "| id | action | owner | due | source | status |\n|---|---|---|---|---|---|\n"
        + "| A-001 | Draft mapping set | Product Owner | 2026-11-01 | [[2026-10-20-kickoff-summary]] | open |\n",
        "wiki/meetings/2026-10-20-kickoff-summary.md": fm(type="meeting", title="Kickoff", tags="[meeting]",
                                                           status="unreviewed", scope="compliance-agent",
                                                           created="2026-10-20", updated="2026-10-20",
                                                           held="2026-10-20", sources="[2026-10-20-kickoff-notes]")
        + "Kickoff of [[compliance-agent]]. Actions in [[actions]]. Plan: [[pilot-plan]].\n",
        "wiki/plans/pilot-plan.md": fm(type="plan", title="Pilot plan", tags="[plan]", status="active",
                                       scope="compliance-agent", created="2026-10-20", updated="2026-10-20")
        + "Milestone: pilot review on 2026-11-14 for [[compliance-agent]].\n",
        "meta/regression-cases.md": "# Cases\n",
        "wiki/index.md": fm(type="index", title="Index", tags="[hub]")
        + "- [[agent-design]]\n- [[adr-0001-use-skills]]\n- [[compliance-agent]]\n- [[kyc]]\n- [[pilot-plan]]\n"
        + "- [[2026-10-20-kickoff-summary]]\n- [[actions]]\n",
        "wiki/log.md": fm(type="log", title="Log", tags="[log]")
        + "## 2026-10-20 \u2014 Ingest kickoff\n- added [[agent-design]]\n\n## 2026-10-19 \u2014 Setup\n- scaffold\n",
        "wiki/overview.md": fm(type="overview", title="Overview", tags="[hub]", updated="2026-10-20")
        + "This brain supports the [[compliance-agent]] project.\n",
        "raw/2026-10-20-kickoff-notes.md": fm(type="source", title="Kickoff", tags="[meeting]", status="captured",
                                              captured="2026-10-20", origin="meeting")
        + "\u041a\u0438\u0440\u0438\u043b\u0438\u0446\u0430 \u0442\u0435\u043a\u0441\u0442 ok. Agents map rules to controls.\n",
        "wiki/projects/compliance-agent.md": fm(type="project", title="Compliance agent", tags="[project]",
                                                 status="partially_verified", scope="compliance-agent",
                                                 created="2026-10-20", updated="2026-10-20",
                                                 sources="[2026-10-20-kickoff-notes]")
        + "Goal: map rules to controls. See [[agent-design]] and [[kyc]].\n",
        "wiki/concepts/agent-design.md": fm(type="concept", title="Agent design", tags="[concept]",
                                             status="unreviewed", created="2026-10-20", updated="2026-10-20")
        + "Planner plus critic pattern, see [[compliance-agent]].\n",
        "wiki/decisions/adr-0001-use-skills.md": fm(type="decision", title="Use skills", tags="[decision]",
                                                     status="accepted", scope="compliance-agent",
                                                     created="2026-10-20", updated="2026-10-20")
        + "We use Copilot skills for workflows. Context: [[compliance-agent]].\n",
        "wiki/glossary/kyc.md": fm(type="term", title="KYC", tags="[term]", status="unreviewed",
                                    created="2026-10-20", updated="2026-10-20")
        + "Know Your Customer: identity checks before onboarding.\n",
    }
    for name in ["brain-ingest", "brain-query", "brain-correct", "brain-decision", "brain-lint",
                 "brain-meeting", "brain-status"]:
        good[f".github/skills/{name}/SKILL.md"] = fm(name=name, description="test skill") + "Steps.\n"

    def build(tmp, files):
        for rel, content in files.items():
            p = Path(tmp) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)

    proj = "wiki/projects/compliance-agent.md"
    cases = [
        ("clean brain has no errors", {}, None, set()),
        ("broken link", {proj: good[proj] + "Also [[missing-page]].\n"}, None, {"E_BROKEN_LINK"}),
        ("link with path", {proj: good[proj] + "See [[wiki/concepts/agent-design]].\n"}, None, {"E_LINK_HAS_PATH"}),
        ("bad status", {proj: good[proj].replace("partially_verified", "done")}, None, {"E_BAD_STATUS"}),
        ("verified without evidence", {proj: good[proj].replace("partially_verified", "verified")}, None,
         {"E_VERIFIED_NO_EVIDENCE"}),
        ("missing frontmatter", {"wiki/concepts/agent-design.md": "Planner plus critic pattern text.\n"}, None,
         {"E_NO_FRONTMATTER"}),
        ("mojibake", {proj: good[proj] + "\u0420\u00b0\u0420\u00b1\u0420\u0406 broken\n"}, None, {"E_MOJIBAKE"}),
        ("cp1251 bytes", {proj: good[proj].encode("utf-8") + "\u0442\u0435\u043a\u0441\u0442\n".encode("cp1251")},
         None, {"E_ENCODING"}),
        ("truncation marker", {proj: good[proj] + "[rest of file unchanged]\n"}, None, {"E_PLACEHOLDER"}),
        ("template placeholder", {proj: good[proj] + "Owner: {{owner}}\n"}, None, {"E_PLACEHOLDER"}),
        ("secret", {proj: good[proj] + "key ghp_" + "a" * 36 + "\n"}, None, {"E_SECRET"}),
        ("duplicate stem", {"wiki/glossary/agent-design.md": good["wiki/concepts/agent-design.md"].replace(
            "type: concept", "type: term")}, None, {"E_DUPLICATE_STEM"}),
        ("wrong folder type", {"wiki/glossary/kyc.md": good["wiki/glossary/kyc.md"].replace("type: term", "type: concept")},
         None, {"E_TYPE_MISMATCH"}),
        ("bad raw filename", {"raw/kickoff.md": good["raw/2026-10-20-kickoff-notes.md"]}, None, {"E_BAD_FILENAME"}),
        ("log order", {"wiki/log.md": good["wiki/log.md"].replace("2026-10-19", "2026-10-21")}, None, {"E_LOG_ORDER"}),
        ("skill name mismatch", {".github/skills/brain-lint/SKILL.md": fm(name="lint", description="x") + "y\n"},
         None, {"E_SKILL_META"}),
        ("missing required file", {"__delete__": "templates/agent.md"}, None, {"E_MISSING_REQUIRED"}),
        ("unfilled project placeholder", {"AGENTS.md": "Project slug: {{PROJECT_SLUG}}\n"}, None,
         {"E_PLACEHOLDER", "E_NO_PROJECT"}),
        ("note from another project", {"wiki/plans/pilot-plan.md": good["wiki/plans/pilot-plan.md"].replace(
            "scope: compliance-agent", "scope: other-project")}, None, {"W_SCOPE_OTHER"}),
        ("bad meeting filename", {"wiki/meetings/kickoff.md": good["wiki/meetings/2026-10-20-kickoff-summary.md"]},
         None, {"E_BAD_FILENAME"}),
        ("bad plan status", {"wiki/plans/pilot-plan.md": good["wiki/plans/pilot-plan.md"].replace(
            "status: active", "status: unreviewed")}, None, {"E_BAD_STATUS"}),
        ("duplicate action id", {"wiki/actions.md": good["wiki/actions.md"]
                                 + "| A-001 | Other | PO | not stated | [[actions]] | open |\n"}, None, {"E_ACTION_DUP"}),
        ("bad action status", {"wiki/actions.md": good["wiki/actions.md"].replace("| open |", "| finished |")},
         None, {"E_BAD_STATUS"}),
        ("action removed (diff)", {}, {"wiki/actions.md": good["wiki/actions.md"].split("| A-001")[0]},
         {"E_ACTION_REMOVED"}),
        ("raw modified (diff)", {}, {"raw/2026-10-20-kickoff-notes.md": "edited\n"}, {"E_RAW_MODIFIED"}),
        ("log rewritten (diff)", {}, {"wiki/log.md": good["wiki/log.md"].replace("- scaffold", "- changed")},
         {"E_LOG_REWRITTEN"}),
        ("change without log (diff)", {}, {proj: good[proj] + "More text.\n"}, {"W_NO_LOG_ENTRY"}),
    ]
    failed = 0
    for name, mutate, after, expect in cases:
        with tempfile.TemporaryDirectory() as tmp:
            files = dict(good)
            delete = mutate.pop("__delete__", None) if "__delete__" in mutate else None
            files.update(mutate)
            if delete:
                files.pop(delete)
            build(tmp, files)
            if after is None:
                rep = check(tmp, today)
                got = rep.codes()
                ok = (not rep.errors()) if not expect else expect <= got
            else:
                before = snapshot(tmp)
                build(tmp, after)
                got = diff(tmp, before).codes()
                ok = expect <= got
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  expected {sorted(expect)} got {sorted(got)}"))
    print(f"selftest: {len(cases) - failed}/{len(cases)} passed")
    return 0 if failed == 0 else 1


def main(argv):
    # Windows consoles often use a legacy code page; never crash on non-ASCII output.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = list(argv)
    root = Path(__file__).resolve().parent.parent
    today = dt.date.today()
    if "--root" in args:
        i = args.index("--root")
        root = Path(args[i + 1]).resolve()
        del args[i:i + 2]
    if "--today" in args:
        i = args.index("--today")
        today = dt.date.fromisoformat(args[i + 1])
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 2
    cmd = args[0]
    if cmd == "selftest":
        return selftest()
    if cmd == "check":
        rep = check(root, today)
        rep.print()
        return 1 if rep.errors() else 0
    if cmd == "snapshot" and len(args) == 2:
        out = Path(args[1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot(root), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"snapshot written: {out}")
        return 0
    if cmd == "diff" and len(args) == 2:
        old = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        rep = diff(root, old)
        rep.print()
        return 1 if rep.errors() else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
