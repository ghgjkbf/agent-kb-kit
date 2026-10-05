#!/usr/bin/env python3
# coding: utf-8
"""agent-kb-kit installer: initialize a knowledge base and print wiring steps.

Does three things and nothing else:
  1. create the KB skeleton at --root (never overwrites existing files);
  2. probe optional retrieval backends (zvec-grep `zg`, memU CLI) and a
     usable python, so you know what you got;
  3. print copy-paste snippets for wiring the Stop hook and the skills into
     your agent (ZCode / Claude Code / anything that reads SKILL.md).

Stdlib only; ASCII-only source for exotic code-page safety.
"""
import argparse
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parent
TEMPLATE = REPO / "knowledge-base"

# (relative dir) files that must exist in a KB skeleton
SKELETON_DIRS = (
    "inbox", "archive", "norms/inbox-drafts", ".state",
    "memory/facts", "memory/projects", "memory/conversations", "memory/references",
)
SKELETON_FILES = ("INDEX.md", "README.md")


def init_kb(root: pathlib.Path) -> int:
    missing = [d for d in SKELETON_DIRS if not (root / d).is_dir()]
    missing_files = [f for f in SKELETON_FILES if not (root / f).is_file()]
    if not missing and not missing_files:
        print(f"[ok] KB skeleton already present: {root}")
        return 0
    if root.exists() and missing_files:
        # root exists but is not a KB: ask rather than guess
        print(f"[stop] {root} exists but lacks {missing_files}; refusing to mix.")
        print("       Pick another --root or create INDEX.md/README.md there first.")
        return 1
    if TEMPLATE.is_dir():
        shutil.copytree(TEMPLATE, root, dirs_exist_ok=True)
    else:
        for d in SKELETON_DIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
        sys.stderr.write("[warn] template dir missing, created bare skeleton\n")
    # strip git keepers so the user KB is data, not repo plumbing
    for keep in root.rglob(".gitkeep"):
        keep.unlink()
    print(f"[ok] KB initialized: {root}")
    return 0


def probe_backends():
    py = shutil.which("python") or shutil.which("python3") or sys.executable
    zg = shutil.which("zg")
    memu = shutil.which("memu")
    node = shutil.which("node")
    print("[probe] python : %s" % py)
    print("[probe] node   : %s" % (node or "not found (needed for the Stop hook)"))
    print("[probe] zg     : %s" % (zg or "not found - optional; install: npm i -g @zvec/zvec-grep"))
    print("[probe] memu   : %s" % (memu or "not found - optional; file-protocol retrieval still works"))


def wiring(root: pathlib.Path):
    root_fwd = root.as_posix()
    print("""
--- wiring -------------------------------------------------------------
1) Stop hook (session-end reconcile + zg refresh + digest reminder)

   ZCode  : .zcode/settings.json  -> hooks.Stop  -> command:
            node <repo>/hooks/kb-stop-hook.mjs
   Claude : .claude/settings.json -> hooks.Stop -> matcher \"*\" -> same command

   Set env KB_ROOT=%s (or rely on <repo>/knowledge-base default),
   KB_PYTHON / KB_SYNC / ZG_EXE to override the rest.

2) Skills (any agent that reads SKILL.md):
   copy skills/kb-add, skills/kb-maintain, skills/knowledge-base into your
   agent skill root, or point the agent at this repo's skills/ directory.

3) Optional backends:
   zvec-grep (hybrid retrieval + auto index): npm i -g @zvec/zvec-grep
       then: cd %s && zg index
   memU (semantic retrieval): set MEMU_EXE + MEMU_DB, then:
       python <repo>/sync/sync_knowledge_base.py
------------------------------------------------------------------------""" % (root_fwd, root_fwd))


def main():
    ap = argparse.ArgumentParser(description="agent-kb-kit installer")
    ap.add_argument("--root", default=str(REPO / "knowledge-base"),
                    help="knowledge-base root to initialize (never overwrites)")
    ap.add_argument("--probe-only", action="store_true",
                    help="only probe backends, do not create anything")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    if not args.probe_only:
        rc = init_kb(root)
        if rc:
            return rc
    probe_backends()
    wiring(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
