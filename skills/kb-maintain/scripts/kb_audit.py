#!/usr/bin/env python3
# coding: utf-8
"""Read-only health check for the shared knowledge base (default root:
<repo>/knowledge-base, override with KB_ROOT or --root).

Source is ASCII-only on purpose: the `python` first on PATH on this machine is
3.8 and reads .py source in the ANSI code page, so non-ASCII literals in the
source would break it. Chinese markers are therefore written as \\u escapes, and
stdout is forced to UTF-8 so entry names survive the pipe.

Sections reported:
  1. index reconciliation  INDEX.md pointers vs files on disk (dangling/orphan)
  2. frontmatter compliance (date/agent/type/confidence/status present, sane)
  3. environment dependency drift (paths on disk, URLs with a timeout)
  4. retrieval gaps (.state/retrieval-gaps.log)
  5. norm drafts waiting in norms/inbox-drafts
  6. optimize proposals: index/entry ordering, near-duplicate pairs, entries
     that look worth trimming, and the overweight sections inside them

This script never writes. Archiving, moving entries from inbox/ to memory/, and
promoting norms are proposals an agent takes to the user. HTTP probing is the
only network action, and only against URLs already recorded in the entries.

Section 6 adds the optimize pass (ordering, near-duplicates, trim candidates).
It too is advisory only: nothing there counts toward `findings`, and merging,
trimming, archiving and reordering stay proposals until the user says yes.

Judgement rules that keep the report honest and small:

  * Lines recording that something is gone on purpose (removal notes, expired
    facts, "residue" inventories) are skipped for path extraction. Those paths
    are absent by design; flagging them would bury the real findings.
  * Path drift is only reported when the containing directory still exists and
    the leaf is gone - cheap to verify, almost always a stale fact. A path whose
    parent is gone too is listed separately as `path_absent_parent`: it cannot
    be told apart from an intended cleanup, so it is not counted as drift.
  * Every row carries file:line, so the agent's judgement step is cheap.
  * URLs get four verdicts, and only `gone` counts as drift: `gone` is a DNS
    failure (the host does not resolve, so it is certainly unreachable). Any
    HTTP status is reported in its own section and is NOT drift - a 404 on an
    API path or a POST-only endpoint only means there is no GET handler, which
    is not evidence the resource is gone. Timeouts and refused connections are
    `unverifiable-cheaply`.

Env: KB_ROOT overrides the knowledge-base root.
"""
import argparse
import json
import os
import pathlib
import re
import socket
import ssl
import statistics
import sys
import urllib.error
import urllib.request

def _default_kb_root():
    """$KB_ROOT > <repo>/knowledge-base (three levels up from here) > ./knowledge-base."""
    here = pathlib.Path(__file__).resolve().parent
    for base in (here.parents[2], here.parents[3], pathlib.Path.cwd()):
        cand = base / "knowledge-base"
        if cand.is_dir():
            return cand
    return pathlib.Path.cwd() / "knowledge-base"


KB = pathlib.Path(os.environ.get("KB_ROOT") or _default_kb_root())
INDEX = KB / "INDEX.md"
GAPS = KB / ".state" / "retrieval-gaps.log"

# Directories whose *.md files are expected to carry an INDEX.md pointer.
INDEXED_DIRS = ("inbox", "memory/facts", "memory/projects",
                "memory/conversations", "memory/references", "norms")
# Files under those roots that are deliberately absent from the index.
EXEMPT_NAMES = ("README.md", "INDEX.md", "IMPORT-MANIFEST.md")
# memory/imported holds copies of each agent's own memory; it is indexed as a
# manifest, not entry by entry, so it is not part of the orphan check.
EXEMPT_PREFIX = ("memory/imported", "norms/inbox-drafts")

REQUIRED_FM = ("date", "agent", "type", "confidence", "status")
STATUS_OK = ("raw", "curated", "archived", "active")
TYPES_OK = ("fact", "project", "conversation", "reference", "norm")

# Section 6 knobs. DUP_JACCARD is a token-set overlap: 0.55 flags heavy
# restatement without firing on entries that merely share a topic. The trim
# trigger is relative: a body is worth a look when it is long in absolute
# terms and much longer than the median entry.
DUP_JACCARD = 0.55
DUP_MIN_TOKENS = 20
DUP_MAX_PAIRS = 20
TRIM_FACTOR = 1.8
TRIM_MIN_TOKENS = 150
TRIM_MAX_SHOWN = 12

ROW_RE = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.*?)\s*\|\s*([A-Za-z]+)\s*\|"
    r"\s*\[(.*?)\]\((.*?)\)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$")
# A pointer row is parsed by splitting the `|` cells, not by one greedy regex:
# a single row may carry two links ("X \u4e0e Y"), and a greedy regex would swallow
# the rest of the line and invent a dangling pointer that does not exist.
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
TYPE_RE = re.compile(r"^[A-Za-z]+$")
ESCAPED_PIPE = "\x00PIPE\x00"
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)

# Windows path. Spaces and CJK are allowed because real locations carry both
# ("C:\\Program Files\\...", "...\\Desktop\\Win\u89e3\u538b\u7f29.lnk"); the cuts
# below undo the glue prose then causes. Parentheses are deliberately NOT path
# characters: prose wraps paths in them ("(F:\\AI\\memu\\models)\u5168\u90e8\u5728 F").
PATH_CHARS = r"A-Za-z0-9 _.\-\\&+~#$@\u4e00-\u9fff"
PATH_RE = re.compile(r"[A-Za-z]:\\[" + PATH_CHARS + r"]+")
URL_RE = re.compile(r"https?://[^\s`\"'()\[\]<>|]+")
# Glue cuts: prose right after the path, a trailing CLI flag, a "+ X" clause,
# or an "X \u4e0e Y" clause. Applied by earliest cut position.
CUT_RES = (
    re.compile(r" [\u4e00-\u9fff]"),
    re.compile(r"\s-[A-Za-z]"),
    re.compile(r" \+ "),
    re.compile(r"\s+\u4e0e\s+"),
)
# A character right after the match that means the pattern stopped mid-token:
# `...\agnes-{image,video}` and `<key>` samples are not real locations.
SAMPLE_TAILS = ("{", "<", "%", "*")
PLACEHOLDER_RE = re.compile(r"[<>%*{}]|\.\.\.")
# A line carrying any of these markers records an absence rather than asserting
# a live location: expired / removed / absent / dropped / broken / uninstalled /
# residue inventory / empty shell / deleted.
SKIP_MARKERS = (
    "\u5df2\u5931\u6548", "\u5df2\u6e05\u9664", "\u5df2\u5220\u9664",
    "\u5df2\u5220", "\u5df2\u4e0d\u5b58\u5728", "\u5df2\u4e0d\u518d",
    "\u5df2\u65e0", "\u5df2\u5254", "\u4e0d\u5b58\u5728", "\u5220\u9664",
    "\u65ad\u94fe", "\u65ad\u5f00", "\u5378\u8f7d", "\u6b8b\u7559", "\u7a7a\u58f3",
)
TRAILING = ".,;:)&+~#@"

# Tokenizer for the duplicate/trim heuristics. CJK has no spaces, so runs of Han
# characters become overlapping bigrams; ASCII words are matched whole and the
# very short ones plus a small stop list are dropped as noise.
WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
STOPWORDS = frozenset((
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "not", "but", "you", "can", "all", "any", "has", "have", "its", "into",
    "when", "then", "than", "them", "they", "will", "would", "should", "your",
    "our", "out", "off", "one", "two", "use", "used", "using", "get", "got",
))
H2_RE = re.compile(r"^##\s+(.*\S)\s*$")
UPDATE_HEADING = "\u66f4\u65b0\u8bb0\u5f55"


def rel(p):
    try:
        return p.relative_to(KB).as_posix()
    except ValueError:
        return str(p)


def read_text(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def parse_front_matter(text):
    m = FM_RE.match(text)
    fm = {}
    if not m:
        return fm, text
    for line in m.group(1).splitlines():
        if not line or line[0] in " \t-" or ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip().lower()] = v.strip().strip("'\"")
    return fm, text[m.end():]


def parse_index():
    rows, problems = [], []
    if not INDEX.exists():
        return rows, ["INDEX.md not found: %s" % INDEX]
    for raw in read_text(INDEX).splitlines():
        line = raw.rstrip("\r\n").strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.replace("\\|", ESCAPED_PIPE).split("|")]
        # Drop the leading and trailing empty cells an outer `|` leaves behind.
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if len(cells) < 6 or not DATE_RE.match(cells[0]):
            continue                          # header or rule row
        date, entry, type_, loc = cells[0], cells[1], cells[2], cells[3]
        writer, status = cells[4], cells[5]
        links = LINK_RE.findall(loc)
        if not links:
            problems.append(line[:160])
            continue
        targets = [t.strip().replace("\\", "/") for _, t in links if t.strip()]
        if not targets:
            problems.append(line[:160])
            continue
        rows.append({"date": date, "entry": entry, "type": type_,
                     "target": targets[0], "targets": targets,
                     "writer": writer, "status": status,
                     "line": line})
    return rows, problems


def iter_entry_files():
    for sub in INDEXED_DIRS:
        root = KB / sub
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            r = rel(p)
            if p.name in EXEMPT_NAMES:
                continue
            if any(r.startswith(pref) for pref in EXEMPT_PREFIX):
                continue
            yield p, r


def iter_indexed_lines():
    """Every line of every indexed entry, as (file, line number, text)."""
    for p, r in iter_entry_files():
        for n, line in enumerate(read_text(p).splitlines(), 1):
            yield r, n, line


def section_index(rows, parse_problems):
    res = {"pointers": len(rows), "dangling": [], "orphan": [], "bad_rows": parse_problems}
    targets = set()
    for row in rows:
        for t in row.get("targets") or [row["target"]]:
            t = t.split("#", 1)[0]
            targets.add(t)
            if not (KB / t).exists():
                res["dangling"].append({"date": row["date"], "entry": row["entry"],
                                         "target": t})
    for p, r in iter_entry_files():
        if r not in targets:
            res["orphan"].append(r)
    return res


def section_frontmatter():
    bad, checked = [], 0
    for p, r in iter_entry_files():
        checked += 1
        fm, _ = parse_front_matter(read_text(p))
        missing = [k for k in REQUIRED_FM if not fm.get(k)]
        notes = []
        if fm.get("status") and fm["status"] not in STATUS_OK:
            notes.append("status=%s" % fm["status"])
        if fm.get("type") and fm["type"] not in TYPES_OK:
            notes.append("type=%s" % fm["type"])
        if missing:
            notes.append("missing: " + ",".join(missing))
        if notes:
            bad.append({"file": r, "notes": notes})
    return {"checked": checked, "issues": bad}


def unglue(token):
    """Trim prose or CLI tails the path pattern swallowed."""
    best = len(token)
    for rx in CUT_RES:
        m = rx.search(token)
        if m and m.start() < best:
            best = m.start()
    return token[:best].rstrip(TRAILING + " ")


def extract_paths(line):
    out = []
    for raw in PATH_RE.findall(line):
        tail = line[line.find(raw) + len(raw):]
        if tail[:1] in SAMPLE_TAILS:
            continue
        if "\\\\" in raw:
            continue          # prose-escaped form (F:\\X in JSON/CLI samples)
        token = unglue(raw)
        if len(token) < 5 or len(token) > 240:
            continue
        if PLACEHOLDER_RE.search(token):
            continue
        if token.count("\\") < 2:
            continue                          # X:\Name is not a location claim
        out.append(token)
    return out


def collect_refs():
    paths, urls = {}, {}
    for r, n, line in iter_indexed_lines():
        where = "%s:%d" % (r, n)
        if not any(mark in line for mark in SKIP_MARKERS):
            for token in extract_paths(line):
                paths.setdefault(token, set()).add(where)
        for raw in URL_RE.findall(line):
            token = raw.rstrip(TRAILING + " ")
            if not token.lower().startswith(("http://", "https://")):
                continue
            urls.setdefault(token, set()).add(where)
    return paths, urls


def probe_url(url, timeout):
    """Return ('ok'|'gone'|'http'|'unverifiable', detail).

    Only a DNS failure is 'gone'. Any HTTP status is 'http' and is never counted
    as drift: a 404 usually means no GET handler (API endpoints, a bridge root),
    not a vanished resource.
    """
    ctx = ssl.create_default_context()
    headers = {"User-Agent": "kb-audit/1.0 (+read-only KB health check)"}
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return "ok", "%s %s" % (resp.status, method)
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (403, 405, 501):
                continue                      # server dislikes HEAD; retry with GET
            return "http", "HTTP %s" % exc.code
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), socket.gaierror):
                return "gone", "DNS: %s" % (exc.reason,)
            return "unverifiable", "network: %s" % (exc.reason,)
        except Exception as exc:                                  # noqa: BLE001
            return "unverifiable", "%s: %s" % (type(exc).__name__, exc)
    return "unverifiable", "HEAD rejected and GET not attempted"


def section_env(paths, urls, no_net, timeout, max_urls):
    res = {"paths_checked": 0, "path_drifted": [], "path_absent_parent": [],
           "urls_checked": 0, "url_gone": [], "url_http": [], "unverifiable": []}
    for token in sorted(paths):
        res["paths_checked"] += 1
        target = pathlib.Path(token)
        if target.exists():
            continue
        row = {"path": token, "in": sorted(paths[token])}
        if target.parent.exists():
            res["path_drifted"].append(row)
        else:
            res["path_absent_parent"].append(row)

    if no_net:
        res["skipped_net"] = True
        return res
    for i, url in enumerate(sorted(urls)):
        if i >= max_urls:
            res["urls_skipped"] = len(urls) - max_urls
            break
        res["urls_checked"] += 1
        verdict, detail = probe_url(url, timeout)
        row = {"url": url, "detail": detail, "in": sorted(urls[url])}
        if verdict == "gone":
            res["url_gone"].append(row)
        elif verdict == "http":
            res["url_http"].append(row)
        elif verdict == "unverifiable":
            res["unverifiable"].append(row)
    return res


def section_gaps():
    if not GAPS.exists():
        return {"log": rel(GAPS), "lines": 0, "queries": []}
    counts, lines = {}, 0
    for raw in read_text(GAPS).splitlines():
        line = raw.strip()
        if not line:
            continue
        lines += 1
        m = re.search(r"\[([^\]]+)\]\s*(.*)$", line)
        key = (m.group(1) + " " + m.group(2)).strip() if m else line
        counts[key] = counts.get(key, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"log": rel(GAPS), "lines": lines,
            "queries": [{"query": k, "hits": v} for k, v in top[:15]]}


def section_norms():
    drafts = KB / "norms" / "inbox-drafts"
    if not drafts.is_dir():
        return {"drafts": [], "active": []}
    return {"drafts": [rel(p) for p in sorted(drafts.glob("*.md"))],
            "active": [rel(p) for p in sorted((KB / "norms").glob("*.md"))]}


def tokenize(text):
    """Token multiset for the optimize heuristics (CJK bigrams, ASCII words)."""
    toks = []
    for m in WORD_RE.finditer(text):
        w = m.group(0)
        if w[0].isascii():
            w = w.lower()
            if len(w) < 3 or w in STOPWORDS:
                continue
            toks.append(w)
        else:
            if len(w) < 2:
                continue
            toks.extend(w[i:i + 2] for i in range(len(w) - 1))
    return toks


def entry_body(text):
    """Body with front matter and any `title:` line removed."""
    _, body = parse_front_matter(text)
    kept = [ln for ln in body.splitlines() if not ln.lower().startswith("title:")]
    return "\n".join(kept)


def entry_sections(body):
    """(heading, token count) for each `## ` block, heaviest first."""
    blocks, in_heading = [], False
    for line in body.splitlines():
        m = H2_RE.match(line)
        if m:
            blocks.append([m.group(1), []])
            in_heading = True
            continue
        if in_heading:
            blocks[-1][1].append(line)
        else:
            if not blocks or blocks[0][0] != "(intro)":
                blocks.insert(0, ["(intro)", []])
            blocks[0][1].append(line)
    out = [(h, len(tokenize("\n".join(ls)))) for h, ls in blocks]
    out.sort(key=lambda hc: (-hc[1], hc[0]))
    return [hc for hc in out if hc[1] > 0]


def load_entries():
    """Per-entry facts the optimize pass needs, keyed by KB-relative path."""
    entries = {}
    for p, r in iter_entry_files():
        text = read_text(p)
        body = entry_body(text)
        fm, _ = parse_front_matter(text)
        lines = text.splitlines()
        if lines and lines[0].startswith("# "):
            heading = lines[0][2:].strip()
        else:
            heading = ""
        toks = tokenize(body)
        entries[r] = {"title": fm.get("title") or heading,
                      "tokens": set(toks), "token_count": len(toks),
                      "lines": len(lines),
                      "bytes": len(text.encode("utf-8")) if isinstance(text, str) else 0,
                      "fm_status": fm.get("status", ""),
                      "fm_date": fm.get("date", ""),
                      "sections": entry_sections(body)}
    return entries


def jaccard(a, b):
    if not a or not b:
        return 0.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


def section_dups(entries):
    """Entry pairs stating largely the same thing (token-set Jaccard)."""
    names = sorted(entries)
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = entries[names[i]], entries[names[j]]
            if len(a["tokens"]) < DUP_MIN_TOKENS or len(b["tokens"]) < DUP_MIN_TOKENS:
                continue
            score = jaccard(a["tokens"], b["tokens"])
            if score >= DUP_JACCARD:
                pairs.append({"a": names[i], "b": names[j],
                              "overlap": round(score, 3),
                              "tokens_a": a["token_count"],
                              "tokens_b": b["token_count"]})
    pairs.sort(key=lambda d: (-d["overlap"], d["a"], d["b"]))
    return pairs[:DUP_MAX_PAIRS]


def section_trim(entries):
    """Long bodies worth reading for cuts, with their heaviest sections."""
    counts = sorted(e["token_count"] for e in entries.values() if e["token_count"])
    median = statistics.median(counts) if counts else 0
    out = []
    for name in sorted(entries):
        e = entries[name]
        if e["token_count"] < TRIM_MIN_TOKENS or e["token_count"] <= TRIM_FACTOR * median:
            continue
        heavy = [{"section": h, "tokens": n} for h, n in e["sections"]
                 if n >= TRIM_FACTOR * median][:4]
        out.append({"file": name, "title": e["title"], "tokens": e["token_count"],
                    "lines": e["lines"], "median": median,
                    "heavy_sections": heavy,
                    "has_update_log": any(h == UPDATE_HEADING for h, _ in e["sections"])})
    out.sort(key=lambda d: -d["tokens"])
    return {"median_tokens": median, "candidates": out[:TRIM_MAX_SHOWN]}


def section_order(rows, entries):
    """ORDER: is the INDEX newest-first, and does each status match the file?"""
    res = {"non_monotonic": [], "status_mismatch": [], "duplicate_targets": []}
    for i in range(len(rows) - 1):
        cur, nxt = rows[i], rows[i + 1]
        if nxt["date"] > cur["date"]:
            res["non_monotonic"].append({"line": i + 3, "entry": nxt["entry"],
                                         "at": nxt["date"], "after": cur["date"]})
    seen = {}
    for row in rows:
        for t in row.get("targets") or [row["target"]]:
            t = t.split("#", 1)[0]
            seen[t] = seen.get(t, 0) + 1
            e = entries.get(t)
            if not e:
                continue
            if e["fm_status"] and row["status"] and e["fm_status"] != row["status"]:
                res["status_mismatch"].append({"file": t, "index": row["status"],
                                               "frontmatter": e["fm_status"]})
    for t, c in sorted(seen.items()):
        if c > 1:
            res["duplicate_targets"].append({"target": t, "rows": c})
    return res


def fmt_index(sec):
    out = ["== 1. index reconciliation ==",
           "  INDEX pointers: %d" % sec["pointers"]]
    for row in sec["bad_rows"]:
        out.append("  ! malformed pointer row: %s" % row)
    if sec["dangling"]:
        out.append("  dangling pointers (INDEX -> missing file): %d" % len(sec["dangling"]))
        for d in sec["dangling"]:
            out.append("    - %s  %s" % (d["target"], d["entry"][:70]))
    else:
        out.append("  dangling pointers: 0")
    if sec["orphan"]:
        out.append("  orphans (file on disk, no INDEX row): %d" % len(sec["orphan"]))
        for o in sec["orphan"]:
            out.append("    - %s" % o)
    else:
        out.append("  orphans: 0")
    return out


def fmt_frontmatter(sec):
    out = ["", "== 2. frontmatter ==", "  checked: %d" % sec["checked"]]
    for issue in sec["issues"]:
        out.append("  ! %s  %s" % (issue["file"], "; ".join(issue["notes"])))
    if not sec["issues"]:
        out.append("  all entries carry date/agent/type/confidence/status")
    return out


def fmt_env(sec):
    out = ["", "== 3. environment dependency drift ==",
           "  paths checked: %d" % sec["paths_checked"],
           "  drifted (parent dir exists, leaf gone): %d" % len(sec["path_drifted"])]
    for d in sec["path_drifted"]:
        out.append("  ! missing: %s  (claimed in %s)" % (d["path"], ", ".join(d["in"])))
    if sec["path_absent_parent"]:
        out.append("  parent gone too (cannot tell from intended cleanup, %d):"
                   % len(sec["path_absent_parent"]))
        for d in sec["path_absent_parent"][:12]:
            out.append("    ? %s  (in %s)" % (d["path"], ", ".join(d["in"])))
        extra = len(sec["path_absent_parent"]) - 12
        if extra > 0:
            out.append("    ... and %d more" % extra)
    if sec.get("skipped_net"):
        out.append("  urls: skipped (--no-net)")
        return out
    out.append("  urls checked: %d" % sec["urls_checked"])
    if sec.get("urls_skipped"):
        out.append("  urls not probed this run (limit): %d" % sec["urls_skipped"])
    for d in sec["url_gone"]:
        out.append("  ! host does not resolve: %s  %s  (in %s)"
                   % (d["url"], d["detail"], ", ".join(d["in"])))
    for d in sec["url_http"]:
        out.append("  ? http status (not drift; API paths answer 404 to GET): %s  %s  (in %s)"
                   % (d["url"], d["detail"], ", ".join(d["in"])))
    for d in sec["unverifiable"]:
        out.append("  ? unverifiable-cheaply: %s  %s  (in %s)"
                   % (d["url"], d["detail"], ", ".join(d["in"])))
    if not sec["url_gone"]:
        out.append("  no host failed to resolve")
    return out


def fmt_gaps(sec):
    out = ["", "== 4. retrieval gaps ==", "  %s: %d lines" % (sec["log"], sec["lines"])]
    for q in sec["queries"]:
        out.append("    x%d  %s" % (q["hits"], q["query"]))
    if not sec["queries"]:
        out.append("  (no gaps recorded)")
    return out


def fmt_norms(sec):
    out = ["", "== 5. norms ==",
           "  active: %d" % len(sec["active"]),
           "  drafts awaiting confirmation: %d" % len(sec["drafts"])]
    for d in sec["drafts"]:
        out.append("    - %s" % d)
    return out


def fmt_optimize(order, dups, trim, entries_seen):
    out = ["", "== 6. optimize proposals (advisory; never counted as findings) ==",
           "  entries scanned: %d  (memory/imported and norms/inbox-drafts excluded)"
           % entries_seen,

           "  -- ordering --"]
    if order["non_monotonic"]:
        out.append("  ! INDEX not newest-first at %d row(s):" % len(order["non_monotonic"]))
        for d in order["non_monotonic"][:10]:
            out.append("    row %d  %s  (%s after %s)"
                       % (d["line"], d["entry"][:56], d["at"], d["after"]))
    else:
        out.append("  date order is monotonic (newest first)")
    if order["status_mismatch"]:
        out.append("  ? INDEX status disagrees with the entry front matter:")
        for d in order["status_mismatch"][:12]:
            out.append("    %s  index=%s  frontmatter=%s"
                       % (d["file"], d["index"], d["frontmatter"]))
    else:
        out.append("  INDEX status matches every entry front matter")
    if order["duplicate_targets"]:
        out.append("  ! the same file is pointed at by more than one row:")
        for d in order["duplicate_targets"]:
            out.append("    %sx  %s" % (d["rows"], d["target"]))

    out.append("  -- redundant / near-duplicate pairs --")
    if dups:
        out.append("  token-set overlap >= %.2f across %d pair(s); read both, then"
                   % (DUP_JACCARD, len(dups)))
        out.append("  merge the thinner one into the fuller one and archive it:")
        for d in dups:
            out.append("    %.2f  %s  (%d tok)" % (d["overlap"], d["a"], d["tokens_a"]))
            out.append("          %s  (%d tok)" % (d["b"], d["tokens_b"]))
    else:
        out.append("  none; no pair of indexed entries restates the other")

    out.append("  -- trim candidates (>%.1fx median body = %d tokens) --"
               % (TRIM_FACTOR, trim["median_tokens"]))
    if trim["candidates"]:
        for c in trim["candidates"]:
            out.append("    %s  %d tokens / %d lines  %s"
                       % (c["file"], c["tokens"], c["lines"], c["title"][:40]))
            for h in c["heavy_sections"]:
                out.append("        heavy section: %s  (%d tokens)" % (h["section"][:52], h["tokens"]))
            if c["has_update_log"]:
                out.append("        carries an update log; fold entries into it, do not rewrite history")
    else:
        out.append("  none; every body is within %.1fx of the median" % TRIM_FACTOR)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="read-only KB health check")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    ap.add_argument("--no-net", action="store_true", help="skip URL probing")
    ap.add_argument("--timeout", type=float, default=6.0, help="URL timeout seconds")
    ap.add_argument("--max-urls", type=int, default=20, help="URLs probed per run")
    ap.add_argument("--fail-on-findings", action="store_true",
                    help="exit 1 when dangling/orphan/drifted is non-empty")
    ap.add_argument("--root", help="override KB root")
    args = ap.parse_args(argv)

    global KB, INDEX, GAPS
    if args.root:
        KB = pathlib.Path(args.root)
        INDEX = KB / "INDEX.md"
        GAPS = KB / ".state" / "retrieval-gaps.log"

    rows, parse_problems = parse_index()
    idx = section_index(rows, parse_problems)
    fm = section_frontmatter()
    paths, urls = collect_refs()
    env = section_env(paths, urls, args.no_net, args.timeout, args.max_urls)
    gaps = section_gaps()
    norms = section_norms()

    entries = load_entries()
    order = section_order(rows, entries)
    dups = section_dups(entries)
    trim = section_trim(entries)
    entries_seen = len(entries)

    advisory = (len(order["non_monotonic"]) + len(order["status_mismatch"])
                + len(order["duplicate_targets"]) + len(dups)
                + len(trim["candidates"]))

    findings = (len(idx["dangling"]) + len(idx["orphan"])
                + len(env["path_drifted"]) + len(env["url_gone"]))

    if args.json:
        payload = {"root": str(KB), "findings": findings, "index": idx,
                   "frontmatter": fm, "environment": env, "gaps": gaps, "norms": norms}
        payload = {"root": str(KB), "findings": findings, "index": idx,
                   "frontmatter": fm, "environment": env, "gaps": gaps,
                   "norms": norms,
                   "optimize": {"entries_seen": entries_seen, "order": order,
                                "duplicates": dups, "trim": trim,
                                "advisory_proposals": advisory}}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        lines = ["kb-audit  %s" % KB, "read-only; no files were changed", ""]
        lines += (fmt_index(idx) + fmt_frontmatter(fm) + fmt_env(env)
                  + fmt_gaps(gaps) + fmt_norms(norms))
        lines += fmt_optimize(order, dups, trim, entries_seen)
        lines += ["", "findings (dangling + orphan + drifted): %d" % findings,
                  "advisory optimize proposals (not findings): %d" % advisory,
                  "next: take the lists above to the user as proposals; nothing is auto-fixed."]
        print("\n".join(lines))

    if args.fail_on_findings and findings:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass
    sys.exit(main())
