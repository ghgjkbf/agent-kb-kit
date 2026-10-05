# 共享知识库(写入协议 v1)

多个 AI Agent 共用的外置知识库。目录即契约——本文件是权威协议,动库前先读它。

## 目录结构

| 目录 | 用途 | 进检索 |
|---|---|---|
| `inbox/` | 新条目落点(raw 状态),定期蒸馏归位 | 是 |
| `memory/facts/` | 稳定环境事实 | 是 |
| `memory/projects/` | 项目上下文与进行中的工作 | 是 |
| `memory/conversations/` | 对话沉淀 | 是 |
| `memory/references/` | 外部资源指针 | 是 |
| `norms/` | **生效中**的行为规范(升格须用户确认) | 是 |
| `norms/inbox-drafts/` | 规范草案,待裁决 | 否 |
| `archive/` | 归档条目(不删除,带 `archived-reason`) | 否 |
| `.state/` | 运行时状态(同步指纹、检索盲区日志) | 否 |

## 条目格式

文件名 `<YYYY-MM-DD>-<英文短名>.md`,frontmatter 必带:

```yaml
---
date: 2026-01-01
agent: <写入者标识>
type: fact | project | conversation | reference | norm
confidence: high | medium | low
status: raw | curated | archived
title: <一句话标题,便于检索>
---
```

`type: norm` 一律落 `norms/inbox-drafts/`,**严禁直接写 `norms/` 生效区**;升格必须用户明确确认。

## 写入流程(四步,顺序固定)

1. **定类型**:按上表选 `type` 与 `confidence`;
2. **查重**:跑 `kb-add` 技能的 `check`(语义检索 + INDEX/文件名回找);命中同主题 → 用 `append` 改原条目,不新建文件;
3. **写入**:用 `kb-add` 技能 `new`(自动落 inbox、写 frontmatter、在 `INDEX.md` 表格首行插指针);手工写入也必须在 INDEX.md 加指针行;
4. **对账**:写入不会自动进检索索引——用 `kb-add` 收尾自动对账,或手工跑 `sync/sync_knowledge_base.py` + `zg index`。未对账的检索命中旧快照。

## INDEX.md

唯一条目总索引,自新到旧,格式:

```
| 日期 | 条目 | 类型 | 位置 | 写入者 | 状态 |
```

状态列与条目 frontmatter 的 `status` 保持一致。`kb-maintain` 体检会报悬空指针与磁盘孤儿。

## 蒸馏与归档

- inbox 条目定期蒸馏归位:`memory/<分类>/` + `status: curated`,INDEX 同步改;
- 过期/被推翻条目移 `archive/`,文件头加 `archived-reason:`,INDEX 状态改 `archived`,不物理删除;
- 近似冗余条目:薄的并入权威条目(内容不丢),原件归档。

## 边界(不可违反)

- 本库是外置增量层:**不迁移、不删改任何 Agent 的本体记忆目录**;
- 单一真值:同一事实与本库已确认条目冲突时,以本库为准;新结论标注取代关系,不静默覆盖;
- 本协议的修改需用户确认;
- 归位、归档、规范升格:先提议、待确认,再动手。
