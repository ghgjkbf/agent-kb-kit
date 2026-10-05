# agent-kb-kit

English | [中文](README.zh-CN.md)

A file-first **shared knowledge base kit for AI agents**: multiple agents (ZCode, Claude Code, Codex, or anything that reads SKILL.md) write into one plain-markdown knowledge base, and every retrieval index stays a disposable cache that can be rebuilt with one command.

Born from a production setup where four agents share one knowledge base on a single machine — distilled into a generic, path-free kit.

## Why

Agents forget between sessions, and each agent's private memory is a silo. This kit gives them one **external, incremental memory layer**:

- **Markdown is the single source of truth.** Human-readable, git-diffable, agent-writable. No database owns your knowledge.
- **Every index is a cache.** Retrieval backends (vector, hybrid search) are optional and rebuildable; losing them loses nothing.
- **Write protocol with dedup.** New entries land in `inbox/`, get reconciled into place, and norms (behavior rules) require explicit human sign-off before taking effect.
- **Sync is automatic and safe.** A Stop hook reconciles indexes at session end; locks prevent duplicate commits; fingerprints skip no-op runs.

## Architecture

```
            ┌────────────────────── your agents ──────────────────────┐
            │  kb-add (write one)   kb-maintain (audit + propose)     │
            │           knowledge-base (read entry point)             │
            └───────────────┬─────────────────────────────────────────┘
                            │ write                          read
                            ▼                                ▼
                 knowledge-base/  ◄────  INDEX.md + grep (always works)
                 inbox/ memory/ norms/ archive/
                            │
              auto-reconcile│(stop hook / kb-add / cron)
                            ▼
        ┌───────────────────┼─────────────────────┐
        ▼ optional           ▼ optional            
   zvec-grep (`zg`)        memU bridge          any future backend
   hybrid fts+vector       semantic vectors     (index is a cache —
   file:line results       chunk results         swap it, lose nothing)
```

## Quickstart

```bash
git clone https://github.com/<you>/agent-kb-kit.git
cd agent-kb-kit
python install.py                # init knowledge-base/ + probe backends + print wiring
```

Then wire your agent (snippets are printed by `install.py`):

1. **Stop hook** — session-end reconcile + index refresh + "digest this?" reminder:
   - ZCode: `.zcode/settings.json` → `hooks.Stop` → `node <repo>/hooks/kb-stop-hook.mjs`
   - Claude Code: `.claude/settings.json` → `hooks.Stop` → same command
2. **Skills** — copy `skills/` (kb-add, kb-maintain, knowledge-base) into your agent's skill root, or point the agent at this directory.
3. **Optional backends** — see below. Without them everything still works through the file protocol (INDEX.md + grep).

## Retrieval backends (all optional)

| Backend | What you get | Install | Missing → |
|---|---|---|---|
| File protocol (built in) | `INDEX.md` lookup + grep/ripgrep; exact keywords | nothing | — |
| [zvec-grep `zg`](https://github.com/zvec-ai/zvec-grep) | hybrid semantic + BM25 + regex, `file:line` results, ~1 s incremental re-index | `npm i -g @zvec/zvec-grep` then `cd <kb-root> && zg index` | silently skipped |
| [memU](https://github.com/NevaMind-AI/memU) | semantic vector recall (chunks) | point `MEMU_EXE` at the CLI, `MEMU_DB` at its sqlite dir | silently skipped |

## Configuration

All environment variables are optional; defaults are auto-detected:

| Variable | Meaning | Default |
|---|---|---|
| `KB_ROOT` | knowledge-base root directory | `<repo>/knowledge-base` |
| `KB_PYTHON` | interpreter for the sync script | `python` on PATH |
| `KB_SYNC` | KB→memU reconcile script path | `<repo>/sync/sync_knowledge_base.py` |
| `MEMU_EXE` | memU CLI executable | `memu` on PATH |
| `MEMU_DB` | memU sqlite db dir (enables diff reporting) | unset → skip diff check |
| `ZG_EXE` | zvec-grep CLI | `zg` on PATH |

## The knowledge base layout

```
knowledge-base/
├── INDEX.md            # single entry index, newest first
├── README.md           # the write protocol (authoritative)
├── inbox/              # new entries (status: raw), distilled into place
├── memory/{facts,projects,conversations,references}/
├── norms/              # ACTIVE norms (promotion needs user sign-off)
│   └── inbox-drafts/   # norm drafts awaiting decision
├── archive/            # archived entries, never deleted
└── .state/             # sync fingerprints, retrieval-gap log (runtime)
```

Write path in one line: **dedup → write to inbox → register in INDEX → reconcile indexes.** The `kb-add` skill does all four; full protocol in [`knowledge-base/README.md`](knowledge-base/README.md).

## Skills

| Skill | Role | Trigger examples |
|---|---|---|
| `knowledge-base` | read entry point: zg → memU → file protocol | “查知识库”, “之前定过什么” |
| `kb-add` | capture ONE entry (dedup → write → index → sync) | “记住这个”, “存知识库” |
| `kb-maintain` | read-only audit: index drift, frontmatter, dead links, distillation proposals | “体检知识库”, “整理知识库” |

## Privacy

Your knowledge base holds your environment's facts and decisions. The kit ships an empty template — **never commit a populated KB to a public repo**, and read entries before sharing them across trust boundaries.

## License

[Apache-2.0](LICENSE)
