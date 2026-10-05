# agent-kb-kit(中文说明)

面向 AI Agent 的**文件优先共享知识库套件**:多个 Agent(ZCode、Claude Code、Codex,或任何读 SKILL.md 的端)向同一个纯 markdown 知识库写入,所有检索索引都是"可丢弃缓存"——一条命令即可重建。

源自一套四 Agent 共用一个知识库的生产环境,抽取通用化而成:不含任何机器专属路径。

## 为什么

Agent 跨会话会失忆,而各家本体记忆互不相通。本套件提供一层**外置增量记忆**:

- **Markdown 是唯一真身**。人类可读、可 diff、Agent 天然会写;没有任何数据库拥有你的知识。
- **所有索引都是缓存**。检索后端(向量/混合)可选、可重建;删了也不丢任何知识。
- **带查重的写入协议**。新条目落 `inbox/`,蒸馏归位;行为规范(norm)升格必须用户明确确认。
- **对账自动且安全**。Stop 钩子会话收尾对账索引;锁防重复提交;指纹跳过无变动。

## 架构

```
            ┌────────────────────── 你的各端 Agent ────────────────────┐
            │  kb-add(写一条)      kb-maintain(体检+提议)             │
            │           knowledge-base(读取总入口)                    │
            └───────────────┬─────────────────────────────────────────┘
                            │ 写入                           读取
                            ▼                                ▼
                 knowledge-base/  ◄────  INDEX.md + grep(永远可用)
                 inbox/ memory/ norms/ archive/
                            │
              自动对账       │(Stop 钩子 / kb-add / 计划任务)
                            ▼
        ┌───────────────────┼─────────────────────┐
        ▼ 可选               ▼ 可选
   zvec-grep(zg)          memU 桥              未来任何后端
   混合 fts+向量           语义向量分块          (索引即缓存——
   文件:行号结果           文本块结果             随时换,零损失)
```

## 快速开始

```bash
git clone https://github.com/<you>/agent-kb-kit.git
cd agent-kb-kit
python install.py                # 初始化 knowledge-base/ + 探测后端 + 打印接线指引
```

然后按 `install.py` 打印的指引接线:

1. **Stop 钩子**——会话收尾对账 + 索引刷新 + 沉淀提醒:
   - ZCode:`.zcode/settings.json` → `hooks.Stop` → `node <仓库>/hooks/kb-stop-hook.mjs`
   - Claude Code:`.claude/settings.json` → `hooks.Stop` → 同一命令
2. **技能**——把 `skills/`(kb-add、kb-maintain、knowledge-base)拷进 Agent 技能根目录,或直接指向本目录;
3. **可选后端**——见下表。全不装也能用:文件协议(INDEX.md + grep)永远可用。

## 检索后端(全部可选)

| 后端 | 能得到什么 | 安装 | 缺失时 |
|---|---|---|---|
| 文件协议(内置) | `INDEX.md` 定位 + grep/ripgrep 精确命中 | 无需 | — |
| [zvec-grep `zg`](https://github.com/zvec-ai/zvec-grep) | 语义+BM25+正则一次融合,返回 `文件:行号`,增量重建约 1 秒 | `npm i -g @zvec/zvec-grep`,然后 `cd <知识库根> && zg index` | 静默跳过 |
| [memU](https://github.com/NevaMind-AI/memU) | 本地语义向量召回(文本块) | `MEMU_EXE` 指 CLI,`MEMU_DB` 指 sqlite 目录 | 静默跳过 |

## 配置

环境变量全部可选,缺省自动探测:

| 变量 | 含义 | 默认 |
|---|---|---|
| `KB_ROOT` | 知识库根目录 | `<仓库>/knowledge-base` |
| `KB_PYTHON` | 跑同步脚本的解释器 | PATH 上的 `python` |
| `KB_SYNC` | KB→memU 对账脚本路径 | `<仓库>/sync/sync_knowledge_base.py` |
| `MEMU_EXE` | memU CLI | PATH 上的 `memu` |
| `MEMU_DB` | memU sqlite db 目录(启用差异报告) | 未设则跳过差异核对 |
| `ZG_EXE` | zvec-grep CLI | PATH 上的 `zg` |

## 知识库结构

```
knowledge-base/
├── INDEX.md            # 唯一条目总索引,自新到旧
├── README.md           # 写入协议(权威)
├── inbox/              # 新条目(raw),蒸馏归位
├── memory/{facts,projects,conversations,references}/
├── norms/              # 生效中的规范(升格须用户确认)
│   └── inbox-drafts/   # 规范草案,待裁决
├── archive/            # 归档条目,不物理删除
└── .state/             # 同步指纹、检索盲区日志(运行时)
```

写入一句话流程:**查重 → 落 inbox → 登记 INDEX → 对账索引。** `kb-add` 技能四步全包;完整协议见 [`knowledge-base/README.md`](knowledge-base/README.md)。

## 三个技能

| 技能 | 职责 | 触发示例 |
|---|---|---|
| `knowledge-base` | 读取总入口:zg → memU → 文件协议 | "查知识库"、"之前定过什么" |
| `kb-add` | 捕获一条(查重→写入→登记→对账) | "记住这个"、"存知识库" |
| `kb-maintain` | 只读体检:索引对账、frontmatter、死链、蒸馏提议 | "体检知识库"、"整理知识库" |

## 隐私

知识库存的是你环境的事实与决策。本套件只发空模板——**切勿把装满内容的知识库提交到公开仓库**,跨信任边界分享条目前先读一遍。

## 许可证

[Apache-2.0](LICENSE)
