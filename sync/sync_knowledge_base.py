#!/usr/bin/env python3
# sync_knowledge_base.py — 共享知识库 ⇄ memU memory 轨道 对账同步（幂等，可重复跑）
#
# 背景：KB 文件落盘（手工写 inbox / MCP memory_write）后 memU 向量库不会自动感知，
# `memu retrieve` 命中的仍是旧快照。本脚本以 KB 为唯一真值，全量对账提交。
#
# memU 是可选后端：找不到 memu CLI 时本脚本直接跳过（退出 0），不影响纯文件协议。
#
# 用法：
#   python sync_knowledge_base.py --dry-run
#   python sync_knowledge_base.py --if-changed      # 钩子/计划任务用
#
# 配置（环境变量，均可省略）：
#   KB_ROOT   知识库根目录；默认 <仓库>/knowledge-base，其次 ./knowledge-base
#   MEMU_EXE  memu CLI 路径；默认在 PATH 上找 `memu`
#   MEMU_DB   memU 向量库的 sqlite db 目录（可选；不设则跳过库内差异核对）
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time


def _default_kb_root():
    here = pathlib.Path(__file__).resolve().parent
    for base in (here.parent, pathlib.Path.cwd()):
        cand = base / "knowledge-base"
        if cand.is_dir():
            return cand
    return pathlib.Path.cwd() / "knowledge-base"


KB = pathlib.Path(os.environ.get("KB_ROOT") or _default_kb_root())
MEMU = os.environ.get("MEMU_EXE") or shutil.which("memu") or ""
MEMU_DB = os.environ.get("MEMU_DB", "")
STATE = KB / ".state"
PAYLOAD = STATE / ".kb-payload.json"
MARKER = STATE / ".kb-synced.json"
LOCK = STATE / ".kb-sync.lock"
LOCK_STALE_SEC = 600


class Busy(Exception):
    """另一个同步进程正在持有锁。"""


def acquire_lock():
    """O_EXCL 抢锁；持锁者超过 10 分钟视为僵死可被接管。多个会话同时收尾时防重复提交。"""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            if time.time() - LOCK.stat().st_mtime > LOCK_STALE_SEC:
                LOCK.unlink()
                return acquire_lock()
        except OSError:
            pass
        return False


def release_lock():
    try:
        LOCK.unlink()
    except OSError:
        pass

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
DESC_MAX = 180


def split_front_matter(text):
    """极简 frontmatter 解析：只取顶层 `key: value`，跳过缩进行与分隔符。"""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if not line or line[0] in " \t-" or ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip().lower()] = v.strip().strip("'\"")
    return fm, text[m.end():]


def describe(name, fm, body):
    """描述优先取 frontmatter 的 title/description，其次正文首个 h1，最后文件名。"""
    for key in ("title", "description"):
        v = (fm.get(key) or "").strip()
        if v:
            return v[:DESC_MAX]
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()[:DESC_MAX]
    return name


def collect():
    """扫描 KB 生效条目 → [(name, path)]。imported 保留相对名，其余用 basename。"""
    imported_root = KB / "memory" / "imported"
    picked = []

    if imported_root.is_dir():
        for p in sorted(imported_root.rglob("*.md")):
            if p.name.startswith("IMPORT-MANIFEST"):
                continue  # 清单文件，不是记忆条目
            picked.append((p.relative_to(imported_root).as_posix(), p))

    for sub in ("memory", "inbox", "norms"):
        root = KB / sub
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            if imported_root in p.parents:
                continue
            if "inbox-drafts" in p.parts:
                continue  # 规范草案区未生效，不入检索
            picked.append((p.name, p))

    seen, items = {}, []
    for name, path in picked:
        if name in seen:
            sys.exit(f"名字冲突(同 name 两文件)：{name} → {seen[name]} / {path}")
        seen[name] = path
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        fm, body = split_front_matter(text)
        items.append({
            "name": name,
            "track": "memory",
            "description": describe(name, fm, body),
            "content": text,
        })
    return items


def kb_fingerprint():
    """KB 生效文件的 (相对路径, mtime, size) 指纹，用于判断是否需要重跑全量提交。"""
    rows = []
    for sub in ("memory", "inbox", "norms"):
        root = KB / sub
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            if "inbox-drafts" in p.parts:
                continue
            st = p.stat()
            rows.append(f"{p.relative_to(KB).as_posix()}:{int(st.st_mtime)}:{st.st_size}")
    payload = "\n".join(rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def db_names():
    """memU 库内 memory 轨道已有条目名；未配 MEMU_DB 时返回空集（跳过差异核对）。"""
    if not MEMU_DB:
        return set()
    con = sqlite3.connect(f"file:{MEMU_DB}?mode=ro", uri=True)
    try:
        return {n for (n,) in con.execute("select name from memu_recall_files where track='memory'")}
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="KB ⇄ memU memory 轨道对账同步")
    ap.add_argument("--dry-run", action="store_true", help="只报告差异，不写库")
    ap.add_argument("--verbose", action="store_true", help="逐条列出清单")
    ap.add_argument("--if-changed", action="store_true",
                    help="KB 文件未变动且库内无缺条目时直接退出（供计划任务/钩子调用）")
    ap.add_argument("--quiet", action="store_true", help="无变动时静默（供钩子调用）")
    args = ap.parse_args()

    if not MEMU or not pathlib.Path(MEMU).exists():
        if not args.quiet:
            print("memu CLI 不可用（可选后端，见 README 配置一节）；跳过 memU 对账，纯文件协议不受影响。")
        return 0

    fp = kb_fingerprint()
    prev_fp, prev_state = None, {}
    if MARKER.exists():
        try:
            prev_state = json.loads(MARKER.read_text(encoding="utf-8"))
            prev_fp = prev_state.get("kb_fingerprint")
        except Exception:
            prev_state = {}

    items = collect()
    incoming = {i["name"] for i in items}
    try:
        existing = db_names()
    except Exception as e:
        print(f"读库失败({e})；将按全量提交处理")
        existing = set()

    missing = sorted(incoming - existing)
    orphan = sorted(existing - incoming)
    print(f"KB 生效条目 {len(items)} 条 / memU memory 轨道 {len(existing)} 条")
    print(f"  待新增 {len(missing)} 条 · 库里 KB 无对应文件 {len(orphan)} 条")
    if args.verbose or args.dry_run:
        for n in missing:
            print("  +", n)
        for n in orphan:
            print("  ? 库里多余:", n)

    if args.dry_run:
        print("(dry-run，未写库)")
        return 0

    if args.if_changed and fp == prev_fp and not missing and not orphan:
        if not args.quiet:
            print(f"KB 无变动(指纹 {fp[:12]})且库内无缺条目，跳过提交。")
        return 0

    if not acquire_lock():
        print("另一同步进程正在运行，跳过（下次再对账）。")
        return 0
    try:
        return _commit(items, incoming, fp)
    finally:
        release_lock()


def _commit(items, incoming, fp):
    PAYLOAD.parent.mkdir(parents=True, exist_ok=True)
    PAYLOAD.write_text(json.dumps({"recall_files": items}, ensure_ascii=False), encoding="utf-8")
    t0 = time.time()
    try:
        r = subprocess.run([MEMU, "commit", str(PAYLOAD), "--json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=1800)
    except Exception as exc:
        print(f"memu commit 调用失败（可选后端，已跳过）：{exc}")
        return 1
    try:
        d = json.loads(r.stdout or "{}")
        committed = len(d.get("recall_files", []))
    except Exception:
        print("commit 异常：", (r.stdout or r.stderr)[:400])
        return 1

    after = db_names()
    # 未配 MEMU_DB 时无法核对库内清单，不把"未核验"误报成"遗漏"
    still_missing = sorted(incoming - after) if MEMU_DB else []
    MARKER.write_text(json.dumps({
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kb_fingerprint": fp,
        "kb_entries": len(items),
        "committed": committed,
        "db_memory_total": len(after),
        "still_missing": still_missing,
        "seconds": round(time.time() - t0, 1),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"提交 {committed}/{len(items)} 条，耗时 {time.time() - t0:.0f}s；"
          f"库内 memory 轨道现 {len(after)} 条，遗漏 {len(still_missing)} 条")
    return 0 if not still_missing else 1


if __name__ == "__main__":
    sys.exit(main())
