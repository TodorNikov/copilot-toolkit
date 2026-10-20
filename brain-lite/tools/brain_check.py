"""brain_check.py (lite) - optional safety checks for the project brain.

Standard library only (Python 3.8+). Read-only. Uses git, if available, to compare with the last commit.
It finds format and integrity problems. It does NOT prove that the knowledge is true.

Usage (run from the brain folder):
  python tools/brain_check.py            check everything (also against the last git commit)
  python tools/brain_check.py selftest   verify that this script itself works
Options: --root PATH (default: parent of tools/), --today YYYY-MM-DD
Exit code: 0 = no ERROR, 1 = at least one ERROR, 2 = usage problem.
"""
import datetime as dt
import re
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "lite-1.0"

ROOT_PAGES = ("notes/index.md", "notes/log.md", "notes/overview.md")
REQUIRED_FILES = ["AGENTS.md", "notes/index.md", "notes/log.md", "notes/overview.md"]

STEM_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SOURCE_STEM_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")
LOG_HEAD_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}) (?:" + chr(0x2014) + r"|-) \S.*$")
CATEGORY_ROW_RE = re.compile(r"^\|\s*([a-z0-9]+(?:-[a-z0-9]+)*)\s*\|[^|]*\|\s*([^|]*?)\s*\|\s*$")

PLACEHOLDER_RES = [
    re.compile(r"\{\{[^}\n]+\}\}"),
    re.compile(r"(?i)\brest of (?:the )?(?:file|note|content|document|page)\b"),
    re.compile(r"(?i)\b(?:existing|previous|original) content (?:remains|unchanged|here|goes here)\b"),
    re.compile(r"(?i)\.\.\.\s*\((?:truncated|omitted|unchanged)\)"),
]


def _cls(codes):
    return "[" + "".join(re.escape(chr(c)) for c in codes) + "]"


# Typical double-encoding of Cyrillic (UTF-8 read as cp1252 or cp1251).
MOJIBAKE_RE = re.compile("|".join([
    _cls([0xD0, 0xD1]) + _cls(list(range(0x80, 0xC0)) + [0x2018, 0x2019, 0x201C, 0x201D, 0x2020, 0x2021,
                                                        0x2030, 0x2039, 0x203A, 0x20AC, 0x152, 0x153, 0x160,
                                                        0x161, 0x178, 0x17D, 0x17E, 0x192, 0x2C6, 0x2DC]),
    re.escape(chr(0xE2) + chr(0x20AC)),
    re.escape(chr(0x420)) + _cls([0xB0, 0xB1, 0xB5, 0xB6, 0xB7, 0x406, 0x456, 0x491, 0x451, 0x2116, 0x454, 0x458,
                                  0x405, 0x455, 0x457]),
    re.escape(chr(0x421)) + _cls([0x402, 0x403, 0x201A, 0x453, 0x201E, 0x2020, 0x2021, 0x20AC, 0x2030, 0x409,
                                  0x2039, 0x40A, 0x40C, 0x40B, 0x40F]),
]))
SECRET_RES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bAccountKey=[A-Za-z0-9+/=]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]
POSSIBLE_SECRET_RE = re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*(?!\[REDACTED\])\S{6,}")


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, code, where, msg):
        self.items.append((level, code, where, msg))

    def codes(self):
        return {i[1] for i in self.items}

    def errors(self):
        return [i for i in self.items if i[0] == "ERROR"]

    def print(self):
        order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        for level, code, where, msg in sorted(self.items, key=lambda i: (order[i[0]], i[2], i[1])):
            print(f"{level:5} {code:22} {where} - {msg}")
        e = len(self.errors())
        w = sum(1 for i in self.items if i[0] == "WARN")
        print(f"brain_check {VERSION}: {e} error(s), {w} warning(s) -> {'FAIL' if e else 'PASS'}")


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
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None, text, 1, "frontmatter opened with --- but never closed"
    fields, err = {}, None
    for n, raw in enumerate(lines[1:end], 2):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            err = err or f"line {n}: cannot parse '{line.strip()[:40]}'"
            continue
        key, value = m.group(1), m.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fields[key] = [v.strip().strip("\"'") for v in inner.split(",")] if inner else []
        else:
            fields[key] = value.strip("\"'")
    return fields, "\n".join(lines[end + 1:]), end + 2, err


def strip_code(numbered_lines):
    fence = None
    for n, line in numbered_lines:
        m = re.match(r"^\s*(```|~~~)", line)
        if m:
            fence = m.group(1) if fence is None else (None if fence == m.group(1) else fence)
            continue
        if not fence:
            yield n, re.sub(r"`[^`]*`", "", line)


def read_categories(agents_text):
    """Category folders and their allowed statuses, from the table in AGENTS.md."""
    cats = {}
    for line in agents_text.split("\n"):
        m = CATEGORY_ROW_RE.match(line.strip())
        if m and m.group(1) != "folder":
            cats[m.group(1)] = {s.strip() for s in m.group(2).split(",") if s.strip()}
    return cats


def log_sections(text):
    parts = re.split(r"(?m)^(?=## )", text.replace("\r\n", "\n"))
    return [p.strip() for p in parts if p.startswith("## ")]


def git(root, *args):
    try:
        res = subprocess.run(["git", *args], cwd=str(root), capture_output=True)
    except (FileNotFoundError, OSError):
        return None
    return res.stdout if res.returncode == 0 else None


def check(root, today, r=None):
    r = r or Report()
    root = Path(root)
    for req in REQUIRED_FILES:
        if not (root / req).is_file():
            r.add("ERROR", "E_MISSING_REQUIRED", req, "required file is missing")
    texts = {}
    for p in sorted(root.rglob("*.md")):
        if ".git" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        data = p.read_bytes()
        if data.startswith(b"\xef\xbb\xbf"):
            r.add("WARN", "W_BOM", rel, "UTF-8 BOM present")
            data = data[3:]
        try:
            text = data.decode("utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError as exc:
            r.add("ERROR", "E_ENCODING", rel, f"not valid UTF-8 ({exc.reason} at byte {exc.start})")
            continue
        if chr(0xFFFD) in text:
            r.add("ERROR", "E_ENCODING", rel, "contains replacement characters (lost characters)")
        m = MOJIBAKE_RE.search(text)
        if m:
            r.add("ERROR", "E_MOJIBAKE", f"{rel}:{text.count(chr(10), 0, m.start()) + 1}", "looks like double-encoded text")
        texts[rel] = text

    cats = {}
    if "AGENTS.md" in texts:
        for n, line in strip_code(enumerate(texts["AGENTS.md"].split("\n"), 1)):
            if PLACEHOLDER_RES[0].search(line):
                r.add("ERROR", "E_PLACEHOLDER", f"AGENTS.md:{n}", "fill in the project values")
        cats = read_categories(texts["AGENTS.md"])
        if not cats:
            r.add("ERROR", "E_NO_CATEGORIES", "AGENTS.md", "no category table found")
    skills = root / ".github" / "skills"
    if skills.is_dir():
        for sk in sorted(skills.iterdir()):
            rel = f".github/skills/{sk.name}/SKILL.md"
            if rel in texts:
                fields, _, _, err = split_frontmatter(texts[rel])
                if not fields or err or fields.get("name") != sk.name or not fields.get("description"):
                    r.add("ERROR", "E_SKILL_META", rel, "frontmatter needs name = folder name and a description")

    notes = {rel: t for rel, t in texts.items() if rel.startswith("notes/") or rel.startswith("sources/")}
    stems = {}
    for rel in notes:
        stems.setdefault(Path(rel).stem, []).append(rel)
    for stem, rels in stems.items():
        if len(rels) > 1:
            r.add("ERROR", "E_DUPLICATE_NAME", ", ".join(rels), f"[[{stem}]] would be ambiguous")

    inbound, parsed = {}, {}
    for rel, text in notes.items():
        parts = rel.split("/")
        stem = Path(rel).stem
        fields, body, start, err = split_frontmatter(text)
        is_source = parts[0] == "sources"
        if is_source:
            if len(parts) != 2 or not SOURCE_STEM_RE.match(stem):
                r.add("ERROR", "E_BAD_NAME", rel, "sources are sources/YYYY-MM-DD-name.md")
        elif rel in ROOT_PAGES:
            pass
        elif len(parts) != 3:
            r.add("ERROR", "E_UNKNOWN_LOCATION", rel, "pages live in notes/<category>/<name>.md")
        else:
            if parts[1] not in cats:
                r.add("WARN", "W_UNKNOWN_CATEGORY", rel, f"folder '{parts[1]}' is not in the AGENTS.md table")
            if not STEM_RE.match(stem):
                r.add("ERROR", "E_BAD_NAME", rel, "file names are lowercase-words-with-hyphens.md")
        if fields is None:
            if rel not in ("notes/index.md", "notes/log.md"):
                r.add("ERROR", "E_NO_FRONTMATTER", rel, err or "missing --- frontmatter block")
            fields = {}
        elif err:
            r.add("ERROR", "E_FRONTMATTER", rel, err)
        parsed[rel] = fields
        if is_source:
            for key in ("type", "title", "saved"):
                if not fields.get(key):
                    r.add("ERROR", "E_MISSING_FIELD", rel, f"source needs '{key}'")
        elif len(parts) == 3:
            for key in ("type", "title", "status", "updated"):
                if not fields.get(key):
                    r.add("ERROR", "E_MISSING_FIELD", rel, f"page needs '{key}'")
            if fields.get("type") and fields["type"] != parts[1]:
                r.add("ERROR", "E_TYPE_MISMATCH", rel, f"type '{fields['type']}' must equal the folder '{parts[1]}'")
            allowed = cats.get(parts[1])
            if allowed and fields.get("status") and fields["status"] not in allowed:
                r.add("ERROR", "E_BAD_STATUS", rel, f"status '{fields['status']}' not in {sorted(allowed)}")
            if len(body.strip()) < 20:
                r.add("ERROR", "E_EMPTY", rel, "page has almost no content")
        for key in ("updated", "saved"):
            if fields.get(key):
                d = parse_date(fields[key])
                if d is None:
                    r.add("ERROR", "E_BAD_DATE", rel, f"{key} must be YYYY-MM-DD")
                elif d > today:
                    r.add("WARN", "W_FUTURE_DATE", rel, f"{key} {d} is after today")
        for s in fields.get("sources", []) if isinstance(fields.get("sources"), list) else []:
            s = s.strip("[]")
            if s and not s.startswith(("user", "ext:")) and s not in stems:
                r.add("ERROR", "E_BAD_SOURCE", rel, f"source '{s}' does not exist (use a file name, user or ext:...)")
            elif s in stems:
                inbound.setdefault(s, set()).add(rel)
        for n, line in strip_code(enumerate(body.split("\n"), start)):
            for m in LINK_RE.finditer(line):
                target = m.group(1).split("|", 1)[0].split("#", 1)[0].strip()
                target = target[:-3] if target.endswith(".md") else target
                if "/" in target or "\\" in target:
                    r.add("ERROR", "E_LINK_PATH", f"{rel}:{n}", f"[[{target}]]: use the bare file name")
                elif target not in stems:
                    r.add("ERROR", "E_BROKEN_LINK", f"{rel}:{n}", f"[[{target}]] does not exist")
                else:
                    inbound.setdefault(target, set()).add(rel)
            if any(p.search(line) for p in PLACEHOLDER_RES):
                r.add("ERROR", "E_PLACEHOLDER", f"{rel}:{n}", "placeholder or truncation marker left in the file")
        for n, line in enumerate(body.split("\n"), start):
            if any(p.search(line) for p in SECRET_RES):
                r.add("ERROR", "E_SECRET", f"{rel}:{n}", "looks like a credential or key - remove it")
            elif POSSIBLE_SECRET_RE.search(line):
                r.add("WARN", "W_POSSIBLE_SECRET", f"{rel}:{n}", "possible credential - check it")

    index_links = {t for t, srcs in inbound.items() if "notes/index.md" in srcs}
    for rel in notes:
        stem, parts = Path(rel).stem, rel.split("/")
        if parts[0] == "notes" and len(parts) == 3 and stem not in index_links:
            r.add("WARN", "W_NOT_IN_INDEX", rel, "not listed in notes/index.md")
        if parts[0] == "sources" and stem not in inbound:
            r.add("WARN", "W_UNUSED_SOURCE", rel, "no page refers to this source")

    if "notes/log.md" in texts:
        last = None
        for n, line in strip_code(enumerate(texts["notes/log.md"].split("\n"), 1)):
            if line.startswith("## "):
                m = LOG_HEAD_RE.match(line)
                d = parse_date(m.group(1)) if m else None
                if not d:
                    r.add("ERROR", "E_LOG_HEADING", f"notes/log.md:{n}", "log headings are '## YYYY-MM-DD - what'")
                    continue
                if last and d > last:
                    r.add("ERROR", "E_LOG_ORDER", f"notes/log.md:{n}", "newest entries go on top")
                last = d

    head = git(root, "ls-tree", "-r", "--name-only", "HEAD")
    if head is None:
        r.add("INFO", "I_NO_GIT", ".", "no git history to compare with (first commit not made yet, or no git)")
        return r
    for rel in head.decode("utf-8", "replace").split("\n"):
        if not rel.endswith(".md"):
            continue
        old = git(root, "show", f"HEAD:{rel}")
        old_text = old.decode("utf-8", "replace").replace("\r\n", "\n") if old is not None else ""
        p = root / rel
        if rel.startswith("sources/"):
            if not p.is_file():
                r.add("ERROR", "E_SOURCE_DELETED", rel, "saved sources are never deleted")
            elif p.read_bytes().decode("utf-8", "replace").replace("\r\n", "\n") != old_text:
                r.add("ERROR", "E_SOURCE_CHANGED", rel, "saved sources are never edited")
        elif rel.startswith("notes/") and not p.is_file():
            r.add("WARN", "W_DELETED", rel, "page was deleted - set a status instead")
        if rel == "notes/log.md" and "notes/log.md" in texts:
            current = set(log_sections(texts["notes/log.md"]))
            for sec in log_sections(old_text):
                if sec not in current:
                    r.add("ERROR", "E_LOG_REWRITTEN", rel, f"old entry changed or removed: '{sec.splitlines()[0][:50]}'")
    return r


def selftest():
    cyr = "".join(chr(c) for c in (0x421, 0x440, 0x435, 0x449, 0x430))
    dash = chr(0x2014)
    fm = lambda **kv: "---\n" + "".join(f"{k}: {v}\n" for k, v in kv.items()) + "---\n"
    agents = ("# Rules\n\nProject: Demo\n\n| folder | one page per | status values |\n|---|---|---|\n"
              "| releases | release | planned, released, cancelled |\n| bugs | bug | open, fixed, closed |\n"
              "| meetings | meeting | done |\n\nNever leave `{{placeholders}}`.\n")
    good = {
        "AGENTS.md": agents,
        ".github/skills/brain-save/SKILL.md": fm(name="brain-save", description="save things") + "Steps.\n",
        "notes/index.md": "# Index\n\n## releases\n- [[release-2-4]]\n\n## bugs\n- [[login-timeout]]\n\n"
                          "## meetings\n- [[2026-10-21-release-sync-summary]]\n",
        "notes/log.md": f"# Log\n\n## 2026-10-21 {dash} Saved release sync\n- [[release-2-4]]\n\n"
                        f"## 2026-10-20 {dash} Brain created\n- start\n",
        "notes/overview.md": fm(type="overview", title="Overview", updated="2026-10-21") + "Demo project in short.\n",
        "sources/2026-10-21-release-sync.md": fm(type="source", title="Release sync", saved="2026-10-21",
                                                 origin="meeting") + f"Release 2.4 moves to 12 Nov. {cyr}.\n",
        "notes/releases/release-2-4.md": fm(type="releases", title="Release 2.4", status="planned", updated="2026-10-21",
                                            sources="[2026-10-21-release-sync]")
        + "- 2026-10-21: moved to 12 November because of [[login-timeout]] (source: [[2026-10-21-release-sync]])\n",
        "notes/bugs/login-timeout.md": fm(type="bugs", title="Login timeout", status="open", updated="2026-10-21",
                                          sources="[2026-10-21-release-sync]")
        + "- 2026-10-21: blocks [[release-2-4]]; owner not stated\n",
        "notes/meetings/2026-10-21-release-sync-summary.md": fm(type="meetings", title="Release sync", status="done",
                                                        updated="2026-10-21", sources="[2026-10-21-release-sync]")
        + "Release 2.4 moved, see [[release-2-4]] and [[login-timeout]].\n",
    }

    def build(tmp, files):
        for rel, content in files.items():
            p = Path(tmp) / rel
            if content is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)

    rel_page = "notes/releases/release-2-4.md"
    static = [
        ("clean brain has no errors", {}, set()),
        ("broken link", {rel_page: good[rel_page] + "See [[missing-page]].\n"}, {"E_BROKEN_LINK"}),
        ("link with path", {rel_page: good[rel_page] + "See [[notes/bugs/login-timeout]].\n"}, {"E_LINK_PATH"}),
        ("status not in table", {rel_page: good[rel_page].replace("status: planned", "status: shipped")},
         {"E_BAD_STATUS"}),
        ("type differs from folder", {rel_page: good[rel_page].replace("type: releases", "type: release")},
         {"E_TYPE_MISMATCH"}),
        ("missing frontmatter", {"notes/bugs/login-timeout.md": "Just text without a header, long enough.\n"},
         {"E_NO_FRONTMATTER"}),
        ("mojibake", {rel_page: good[rel_page] + chr(0x420) + chr(0xB0) + chr(0x420) + chr(0xB1) + "\n"},
         {"E_MOJIBAKE"}),
        ("cp1251 bytes", {rel_page: good[rel_page].encode("utf-8") + cyr.encode("cp1251") + b"\n"}, {"E_ENCODING"}),
        ("truncated file", {rel_page: good[rel_page] + "[rest of file unchanged]\n"}, {"E_PLACEHOLDER"}),
        ("secret", {rel_page: good[rel_page] + "ghp_" + "a" * 36 + "\n"}, {"E_SECRET"}),
        ("duplicate name", {"notes/meetings/release-2-4.md": good[rel_page].replace("type: releases", "type: meetings")
                            .replace("status: planned", "status: done")}, {"E_DUPLICATE_NAME"}),
        ("unfilled project", {"AGENTS.md": agents.replace("Project: Demo", "Project: {{PROJECT_NAME}}")},
         {"E_PLACEHOLDER"}),
        ("log order", {"notes/log.md": good["notes/log.md"].replace("2026-10-20", "2026-10-22")}, {"E_LOG_ORDER"}),
        ("new category not in table", {"notes/risks/vendor-delay.md": fm(type="risks", title="Vendor delay",
                                                                         status="open", updated="2026-10-21")
                                       + "Vendor may deliver late; see [[release-2-4]].\n"}, {"W_UNKNOWN_CATEGORY"}),
    ]
    changes = [
        ("source edited (git)", {"sources/2026-10-21-release-sync.md": "edited\n"}, {"E_SOURCE_CHANGED"}),
        ("source deleted (git)", {"sources/2026-10-21-release-sync.md": None}, {"E_SOURCE_DELETED"}),
        ("log rewritten (git)", {"notes/log.md": good["notes/log.md"].replace("- start", "- changed")},
         {"E_LOG_REWRITTEN"}),
        ("page deleted (git)", {"notes/bugs/login-timeout.md": None}, {"W_DELETED"}),
    ]
    today = dt.date(2026, 10, 21)
    failed, total = 0, 0
    for name, mutate, expect in static:
        with tempfile.TemporaryDirectory() as tmp:
            build(tmp, {**good, **mutate})
            rep = check(tmp, today)
            ok = (not rep.errors()) if not expect else expect <= rep.codes()
            total += 1
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {sorted(rep.codes())}"))
    if git(Path.cwd(), "--version") is None:
        print("SKIP  git checks (git not found)")
    else:
        for name, mutate, expect in changes:
            with tempfile.TemporaryDirectory() as tmp:
                build(tmp, good)
                for args in (["init", "-q"], ["add", "-A"],
                             ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"]):
                    git(tmp, *args)
                build(tmp, mutate)
                rep = check(tmp, today)
                ok = expect <= rep.codes()
                total += 1
                failed += not ok
                print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {sorted(rep.codes())}"))
    print(f"selftest: {total - failed}/{total} passed")
    return 0 if failed == 0 else 1


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = list(argv)
    root = Path(__file__).resolve().parent.parent
    today = dt.date.today()
    for flag in ("--root", "--today"):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                print(f"{flag} needs a value")
                return 2
            if flag == "--root":
                root = Path(args[i + 1]).resolve()
            else:
                today = dt.date.fromisoformat(args[i + 1])
            del args[i:i + 2]
    if args == ["selftest"]:
        return selftest()
    if args:
        print(__doc__)
        return 2
    rep = check(root, today)
    rep.print()
    return 1 if rep.errors() else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
