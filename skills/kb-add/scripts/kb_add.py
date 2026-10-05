#!/usr/bin/env python3
# coding: utf-8
"""Mechanical half of the kb-add skill: capture ONE entry into the shared KB.

Source is ASCII-only on purpose: some Windows installs run a `python` that
reads .py source in the ANSI code page, so non-ASCII literals would break it;
every non-ASCII string arrives through argv/stdin and goes back out as UTF-8.

  check  --query TEXT                dedup probe (memu retrieve + INDEX/filename grep)
  new    --title T --type TYPE ...   write the entry and register it in INDEX.md
  append --path FILE --note TEXT     append an update record to an existing entry
  sync                               run the KB -> memU reconcile + zg index refresh

Config (all optional, in resolution order):
  KB_ROOT     knowledge-base root; default: $KB_ROOT > <repo>/knowledge-base
              (derived from this file's location) > ./knowledge-base
  MEMU_EXE    memu CLI for semantic retrieval; default: $MEMU_EXE > `memu` on
              PATH. Absent -> lexical dedup only, sync degrades gracefully.
  KB_SYNC     KB->memU reconcile script; default: <repo>/sync/sync_knowledge_base.py
  KB_SYNC_PY  interpreter for KB_SYNC; default: the current interpreter
  ZG_EXE      zvec-grep CLI; default: `zg` on PATH. Absent -> index refresh skipped.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys


def _default_kb_root():
    """$KB_ROOT > <repo>/knowledge-base (two and three levels up from here) > ./knowledge-base."""
    here = pathlib.Path(__file__).resolve().parent
    for base in (here.parents[2], here.parents[3], pathlib.Path.cwd()):
        cand = base / "knowledge-base"
        if cand.is_dir():
            return cand
    return pathlib.Path.cwd() / "knowledge-base"


KB = pathlib.Path(os.environ.get("KB_ROOT") or _default_kb_root())
INDEX = KB / "INDEX.md"
MEMU = os.environ.get("MEMU_EXE") or shutil.which("memu") or ""
SYNC = os.environ.get(
    "KB_SYNC",
    str(pathlib.Path(__file__).resolve().parents[3] / "sync" / "sync_knowledge_base.py"))
SYNC_PY = os.environ.get("KB_SYNC_PY", sys.executable)
ZG = os.environ.get("ZG_EXE", "")

TYPES = ("fact", "project", "conversation", "reference", "norm")
CONFIDENCE = ("high", "medium", "low")

ROW_RE = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.*?)\s*\|\s*([A-Za-z]+)\s*\|"
    r"\s*\[(.*?)\]\((.*?)\)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$")
SEP_RE = re.compile(r"^\|[\s:\-|]+\|\s*$")
FM_STATUS_RE = re.compile(r"^status:\s*(\S+)", re.M)


def today():
    return datetime.date.today().isoformat()


def fail(msg):
    sys.stderr.write("kb_add: %s\n" % msg)
    return 2


def read_text(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def make_slug(title, hint):
    if hint:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", hint).strip("-") or "entry"
    ascii_part = "-".join(re.findall(r"[A-Za-z0-9]+", title)).lower()[:48].strip("-")
    if ascii_part:
        return ascii_part
    return "entry-" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:8]


def yaml_scalar(text):
    t = (text or "").strip()
    if t == "" or re.search(r"[:#\[\]{}&*!|>'\"%@`]", t):
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return t


def front_matter(date, agent, type_, confidence, status, title):
    lines = ["---",
             "date: %s" % date,
             "agent: %s" % agent,
             "type: %s" % type_,
             "confidence: %s" % confidence,
             "status: %s" % status]
    if title:
        lines.append("title: %s" % yaml_scalar(title))
    lines.append("---")
    return "\n".join(lines) + "\n"


def read_body(spec):
    if not spec:
        return ""
    if spec == "-":
        return sys.stdin.read()
    with open(spec, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def parse_index(text):
    rows = []
    for raw in text.splitlines():
        m = ROW_RE.match(raw.rstrip("\r\n"))
        if m:
            rows.append({"date": m.group(1), "entry": m.group(2), "type": m.group(3),
                         "target": m.group(5), "writer": m.group(6), "status": m.group(7)})
    return rows


def insert_index_row(text, row_line):
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    at = None
    for i, ln in enumerate(lines):
        if SEP_RE.match(ln.rstrip("\r\n")):
            at = i
            break
    if at is None:
        raise SystemExit("kb_add: cannot find the INDEX table separator row")
    lines.insert(at + 1, row_line + nl)
    return "".join(lines)


def refresh_zg():
    """Best-effort zvec-grep index refresh; zg missing or failing is not fatal."""
    zg = ZG or shutil.which("zg")
    if not zg:
        sys.stderr.write("kb_add: zg not found on PATH, zg index not refreshed\n")
        return
    try:
        proc = subprocess.run([zg, "index"], cwd=str(KB),
                              capture_output=True, timeout=300)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
    except Exception as exc:                                  # noqa: BLE001
        sys.stderr.write("kb_add: zg index failed (non-fatal): %s\n" % exc)


def run_sync(quiet=True):
    if not pathlib.Path(SYNC).exists():
        sys.stderr.write("kb_add: sync script not found, skipped: %s\n" % SYNC)
    else:
        try:
            proc = subprocess.run([SYNC_PY, SYNC, "--if-changed"],
                                  capture_output=True, timeout=300)
        except Exception as exc:                              # noqa: BLE001
            sys.stderr.write("kb_add: sync failed to start: %s\n" % exc)
        else:
            if not quiet:
                sys.stdout.write(proc.stdout.decode("utf-8", "replace"))
            if proc.returncode != 0:
                sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
    refresh_zg()


def memu_retrieve(query):
    if not pathlib.Path(MEMU).exists():
        return None, "memu executable not found: %s" % MEMU
    try:
        proc = subprocess.run([MEMU, "retrieve", query, "--json"],
                              capture_output=True, timeout=120)
    except Exception as exc:                                  # noqa: BLE001
        return None, "memu call failed: %s" % exc
    raw = proc.stdout.decode("utf-8", "replace").strip()
    if not raw:
        return None, (proc.stderr.decode("utf-8", "replace").strip() or "empty output")
    try:
        return json.loads(raw), None
    except ValueError as exc:
        return None, "unparsable memu output: %s" % exc


def cmd_check(args):
    tokens = [t for t in re.split(r"[\s,;/|]+", args.query) if len(t) >= 2]
    print("== lexical matches (INDEX.md + file names) ==")
    hits = 0
    if INDEX.exists():
        for raw in read_text(INDEX).splitlines():
            line = raw.rstrip("\r\n")
            if ROW_RE.match(line) and any(t.lower() in line.lower() for t in tokens):
                print("INDEX: " + line)
                hits += 1
    if KB.exists():
        for p in sorted(KB.rglob("*.md")):
            if any(t.lower() in p.name.lower() for t in tokens):
                print("FILE:  " + p.relative_to(KB).as_posix())
                hits += 1
    if hits == 0:
        print("(none)")

    print("\n== semantic matches (memU) ==")
    data, err = memu_retrieve(args.query)
    if err:
        print("(unavailable: %s)" % err)
    else:
        for seg in (data.get("segments") or [])[:5]:
            print("SEG  %.3f  %s" % (seg.get("score", 0.0),
                                     " ".join((seg.get("text") or "").split())[:160]))
        for f in (data.get("files") or [])[:5]:
            print("FILE %.3f  %s" % (f.get("score", 0.0), f.get("name") or ""))
    print("\nverdict: reuse an existing entry (append) if any match is on-topic; "
          "otherwise create a new one.")
    return 0


def cmd_new(args):
    if args.type not in TYPES:
        return fail("--type must be one of: %s" % ", ".join(TYPES))
    if args.confidence not in CONFIDENCE:
        return fail("--confidence must be one of: %s" % ", ".join(CONFIDENCE))

    date = args.date or today()
    slug = make_slug(args.title, args.slug)
    sub = "norms/inbox-drafts" if args.type == "norm" else "inbox"
    rel = "%s/%s-%s.md" % (sub, date, slug)
    path = KB / sub / ("%s-%s.md" % (date, slug))
    body = read_body(args.body)
    if body and not body.endswith("\n"):
        body += "\n"
    text = front_matter(date, args.agent, args.type, args.confidence, "raw", args.title) + "\n" + body

    heading = ""
    for line in body.splitlines():
        if line.startswith("# "):
            heading = line[2:].strip()
            break
    entry_desc = (heading or args.title).replace("|", "\\|")[:200]
    row = "| %s | %s | %s | [%s](%s) | %s | raw |" % (date, entry_desc, args.type, rel, rel, args.agent)

    if path.exists() and not args.force:
        return fail("target already exists: %s  (append to it, or pass --force)" % rel)

    if args.dry_run:
        print("[dry-run] file: %s" % path)
        print("[dry-run] front matter:")
        print(text.split("\n\n")[0])
        print("[dry-run] index row: %s" % row)
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, text)

    index_text = read_text(INDEX)
    if ("(%s)" % rel) in index_text:
        print("note: %s already registered in INDEX.md, row not added twice" % rel)
    else:
        write_text(INDEX, insert_index_row(index_text, row))

    print("written: %s" % path)
    print("index:   %s" % rel)
    if not args.no_sync:
        run_sync()
        print("synced:  KB -> memU + zg index")
    return 0


def cmd_append(args):
    path = pathlib.Path(args.path)
    if not path.is_absolute():
        path = KB / args.path
    if not path.exists():
        return fail("entry not found: %s" % path)
    text = read_text(path)
    date = args.date or today()
    note = args.note.strip()
    if "## \u66f4\u65b0\u8bb0\u5f55" in text:
        text = text.rstrip("\n") + "\n- %s: %s\n" % (date, note)
    else:
        text = text.rstrip("\n") + "\n\n## \u66f4\u65b0\u8bb0\u5f55\n- %s: %s\n" % (date, note)
    write_text(path, text)
    print("appended update record to: %s" % path)
    if not args.no_sync:
        run_sync()
        print("synced:  KB -> memU + zg index")
    return 0


def cmd_sync(args):
    run_sync(quiet=False)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="kb-add mechanical helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="dedup probe before writing")
    p.add_argument("--query", required=True)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("new", help="create one entry and register it")
    p.add_argument("--title", required=True)
    p.add_argument("--type", required=True, dest="type")
    p.add_argument("--agent", required=True)
    p.add_argument("--confidence", default="high")
    p.add_argument("--slug")
    p.add_argument("--date")
    p.add_argument("--body", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-sync", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("append", help="append an update record to an existing entry")
    p.add_argument("--path", required=True)
    p.add_argument("--note", required=True)
    p.add_argument("--date")
    p.add_argument("--no-sync", action="store_true")
    p.set_defaults(func=cmd_append)

    p = sub.add_parser("sync", help="run the KB -> memU reconcile")
    p.set_defaults(func=cmd_sync)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    # Entry titles are Chinese: the stage default here is cp936, so force UTF-8
    # both ways or the report comes back as mojibake.
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                     # noqa: BLE001
            pass
    sys.exit(main())
