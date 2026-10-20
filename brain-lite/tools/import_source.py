"""import_source.py (lite) - turn a meeting transcript or document into a saved source.

Standard library only (Python 3.8+). Needs tools/brain_check.py next to it.
Supported input: .vtt (Teams/Zoom transcripts), .docx, .txt, .md.

Usage (run from the brain folder):
  python tools/import_source.py FILE [--origin meeting] [--date YYYY-MM-DD] [--title "..."] [--slug name]
                                     [--keep-timestamps]
  python tools/import_source.py selftest

Creates sources/YYYY-MM-DD-slug.md with a small header. Never overwrites an existing file.
Removes timestamps and cue numbers from transcripts, merges consecutive lines of the same speaker,
and replaces credentials with [REDACTED]. It does not summarize. The original file is not changed.
"""
import datetime as dt
import re
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain_check as bc  # noqa: E402

VERSION = "lite-1.0"
ORIGINS = {"meeting", "report", "document", "email", "chat", "note", "other"}
MAX_BYTES = 50 * 1024 * 1024
LARGE_LINES = 300

# Bulgarian/Russian Cyrillic a..ya (U+0430..U+044F) to Latin, for file names only.
_LATIN = ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
          "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sht", "a", "y", "y", "e", "yu", "ya"]
TRANSLIT = {0x430 + i: v for i, v in enumerate(_LATIN)}

TIME_RE = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}")
VOICE_RE = re.compile(r"<v(?:\.[^ >]*)?\s+([^>]+)>")
TAG_RE = re.compile(r"</?[^>]+>")
NAME_PREFIX_RE = re.compile(r"^([^:]{2,60}):\s+(.*)$")
VALUE_SECRET_RE = re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)(\s*[:=]\s*)(?!\[REDACTED\])\S{6,}")
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def slugify(text):
    text = text.lower().translate(TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:60].strip("-") or "source"


def read_text(path):
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc), "utf-8" if enc == "utf-8-sig" else enc
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", "replace"), "cp1252"


def parse_vtt(text, keep_timestamps=False):
    """Return a list of (speaker, start, text) turns with consecutive same-speaker lines merged."""
    turns = []
    start, in_note = None, False
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s:
            in_note = False
            continue
        if s.startswith("WEBVTT") or in_note:
            continue
        if s.startswith("NOTE"):
            in_note = True
            continue
        m = TIME_RE.match(s)
        if m:
            start = s.split("-->")[0].strip().split(".")[0].split(",")[0]
            continue
        if start is None:
            continue  # cue identifier before the first timestamp line
        voice = VOICE_RE.search(s)
        speaker = voice.group(1).strip() if voice else None
        body = TAG_RE.sub("", s).strip()
        if speaker is None:
            m = NAME_PREFIX_RE.match(body)
            if m and not re.match(r"^\d", m.group(1)):
                speaker, body = m.group(1).strip(), m.group(2).strip()
        if not body or re.fullmatch(r"[0-9a-f-]{8,}(/\d+-\d+)?", body):
            continue  # Teams cue ids such as 3f1c...-0/12-1
        speaker = speaker or (turns[-1][0] if turns else "Unknown")
        if turns and turns[-1][0] == speaker:
            turns[-1] = (speaker, turns[-1][1], turns[-1][2] + " " + body)
        else:
            turns.append((speaker, start, body))
    lines = []
    for speaker, when, body in turns:
        prefix = f"[{when}] " if keep_timestamps and when else ""
        lines.append(f"{prefix}{speaker}: {body}")
    return "\n\n".join(lines)


def parse_docx(path):
    with zipfile.ZipFile(path) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > MAX_BYTES:
            raise ValueError("document.xml is larger than 50 MB")
        root = ET.fromstring(z.read(info))
    paragraphs = []
    for p in root.iter(W_NS + "p"):
        parts = []
        for el in p.iter():
            if el.tag == W_NS + "t" and el.text:
                parts.append(el.text)
            elif el.tag == W_NS + "tab":
                parts.append("\t")
            elif el.tag in (W_NS + "br", W_NS + "cr"):
                parts.append("\n")
        paragraphs.append("".join(parts).rstrip())
    text = "\n\n".join(paragraphs)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def redact(text):
    count = 0
    for pat in bc.SECRET_RES:
        text, n = pat.subn("[REDACTED]", text)
        count += n
    text, n = VALUE_SECRET_RE.subn(lambda m: m.group(1) + m.group(2) + "[REDACTED]", text)
    return text, count + n


def convert(root, src, origin=None, date=None, title=None, slug=None, keep_timestamps=False):
    """Create the source file. Returns (path, info dict). Raises ValueError on problems."""
    root, src = Path(root), Path(src)
    if not src.is_file():
        raise ValueError(f"file not found: {src}")
    if src.stat().st_size > MAX_BYTES:
        raise ValueError("file is larger than 50 MB")
    ext = src.suffix.lower()
    encoding = "utf-8"
    if ext == ".vtt":
        raw_text, encoding = read_text(src)
        text = parse_vtt(raw_text, keep_timestamps)
        origin = origin or "meeting"
    elif ext == ".docx":
        text = parse_docx(src)
    elif ext in (".txt", ".md"):
        text, encoding = read_text(src)
        text = text.replace("\r\n", "\n").strip()
        if text.startswith("---"):
            text = "\n" + text  # keep a pasted frontmatter-like block out of our frontmatter
    else:
        raise ValueError(f"unsupported file type {ext}: use .vtt, .docx, .txt or .md")
    origin = origin or "other"
    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {sorted(ORIGINS)}")
    if not text.strip():
        raise ValueError("no text found in the file")
    date = date or dt.date.fromtimestamp(src.stat().st_mtime).isoformat()
    if not bc.parse_date(date):
        raise ValueError("date must be YYYY-MM-DD")
    title = (title or src.stem.replace("_", " ").replace("-", " ")).strip()
    title = title.replace('"', "'")
    slug = slugify(slug or title)
    text, redactions = redact(text)
    target = root / "sources" / f"{date}-{slug}.md"
    if target.exists():
        raise ValueError(f"{target.relative_to(root).as_posix()} already exists; sources are never "
                         "overwritten. Use --slug to choose another name.")
    note = (f"---\ntype: source\ntitle: \"{title}\"\nsaved: {date}\norigin: {origin}\n"
            f"original_file: \"{src.name}\"\n---\n\n# {title}\n\n{text}\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(note)
    lines = text.count("\n") + 1
    return target, {"lines": lines, "redactions": redactions, "encoding": encoding, "origin": origin}


def selftest():
    cyr = "".join(chr(c) for c in (0x421, 0x440, 0x435, 0x449, 0x430))  # a Bulgarian word
    vtt = ("WEBVTT\n\n3f1c2a9e-1b2c-4d5e-9f00-112233445566/12-0\n00:00:01.000 --> 00:00:04.000\n"
           "<v Anna Ivanova>Welcome to the kickoff.</v>\n\n"
           "3f1c2a9e-1b2c-4d5e-9f00-112233445566/12-1\n00:00:04.000 --> 00:00:06.000\n"
           "<v Anna Ivanova>Pilot covers three policies.</v>\n\n"
           "00:00:06.000 --> 00:00:09.000\n<v Petar Georgiev>" + cyr + " on Friday. password: Hunter2024!x</v>\n")
    doc_xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
               'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Plan v2</w:t></w:r></w:p><w:p><w:r>'
               '<w:t>Milestone:</w:t><w:tab/><w:t>review 14 Nov</w:t></w:r></w:p></w:body></w:document>')
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "AGENTS.md").write_text("| folder | one page per | status values |\n| bugs | bug | open |\n",
                                        encoding="utf-8")
        (root / "in").mkdir()
        (root / "in" / "kickoff.vtt").write_text(vtt, encoding="utf-8")
        with zipfile.ZipFile(root / "in" / "plan.docx", "w") as z:
            z.writestr("word/document.xml", doc_xml)
        (root / "in" / "notes.txt").write_bytes(("Notes " + cyr + "\n").encode("cp1251"))

        path, info = convert(root, root / "in" / "kickoff.vtt", date="2026-10-21", title="Kickoff meeting")
        out = path.read_text(encoding="utf-8")
        fields, body, _, err = bc.split_frontmatter(out)
        checks.append(("vtt: file name", path.name == "2026-10-21-kickoff-meeting.md"))
        checks.append(("vtt: header parses", err is None and fields.get("type") == "source"
                       and fields.get("saved") == "2026-10-21" and fields.get("origin") == "meeting"))
        checks.append(("vtt: same speaker merged", "Anna Ivanova: Welcome to the kickoff. Pilot covers three policies."
                       in body))
        checks.append(("vtt: timestamps and cue ids removed", "-->" not in body and "3f1c" not in body))
        checks.append(("vtt: cyrillic kept", cyr in body))
        checks.append(("vtt: password redacted", "Hunter2024" not in body and info["redactions"] == 1))
        try:
            convert(root, root / "in" / "kickoff.vtt", date="2026-10-21", title="Kickoff meeting")
            checks.append(("never overwrites", False))
        except ValueError:
            checks.append(("never overwrites", True))
        path, info = convert(root, root / "in" / "plan.docx", origin="document", date="2026-10-22")
        body = path.read_text(encoding="utf-8")
        checks.append(("docx: text and tab", "Plan v2" in body and "Milestone:\treview 14 Nov" in body))
        path, info = convert(root, root / "in" / "notes.txt", origin="note", date="2026-10-22",
                             title=cyr + " notes")
        body = path.read_text(encoding="utf-8")
        checks.append(("txt: cp1251 decoded to utf-8", cyr in body and info["encoding"] == "cp1251"))
        checks.append(("txt: cyrillic title transliterated", path.name == "2026-10-22-sreshta-notes.md"))
        rep = bc.check(root, dt.date(2026, 10, 22))
        bad = [i for i in rep.errors() if i[2].startswith("sources/")]
        checks.append(("created sources pass brain_check", not bad))
    failed = 0
    for name, ok in checks:
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"import selftest: {len(checks) - failed}/{len(checks)} passed")
    return 0 if failed == 0 else 1


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = list(argv)
    if not args:
        print(__doc__)
        return 2
    if args[0] == "selftest":
        return selftest()
    opts = {"origin": None, "date": None, "title": None, "slug": None}
    keep = False
    if "--keep-timestamps" in args:
        args.remove("--keep-timestamps")
        keep = True
    for key in list(opts):
        flag = "--" + key
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                print(f"{flag} needs a value")
                return 2
            opts[key] = args[i + 1]
            del args[i:i + 2]
    if len(args) != 1:
        print(__doc__)
        return 2
    root = Path(__file__).resolve().parent.parent
    try:
        path, info = convert(root, args[0], keep_timestamps=keep, **opts)
    except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"ERROR {exc}")
        return 1
    rel = path.relative_to(root).as_posix()
    print(f"created {rel} ({info['lines']} lines, origin {info['origin']}, read as {info['encoding']})")
    if info["redactions"]:
        print(f"redacted {info['redactions']} credential-like value(s) - check the file")
    if info["encoding"] != "utf-8":
        print(f"note: input was decoded as {info['encoding']}; check that non-English text looks right")
    if info["lines"] > LARGE_LINES:
        print(f"large source: it will be processed in parts of about {LARGE_LINES} lines")
    print(f"next: tell copilot  save {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
