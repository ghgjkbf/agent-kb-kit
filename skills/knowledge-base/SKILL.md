---
name: knowledge-base
description: Use when persisting knowledge for future sessions or OTHER agents, recalling past decisions, user preferences, project background or environment facts, when the user says 记住/存入知识库/查知识库, before answering questions that may have prior context, and during knowledge distillation. Cross-agent shared knowledge base, root set by KB_ROOT (default <repo>/knowledge-base).
---

# Knowledge Base(共享知识库)

所有 Agent 共用的外置知识库,根目录 = `KB_ROOT`(默认 `<仓库>/knowledge-base`,配置见仓库 README)。契约全文见该目录 `README.md`(权威文档,先读它)。

## 快速通道(优先用)

1. **zg 混合检索(推荐,任意 Agent 通用)**:`cd <知识库根> && zg query "问题" --limit 3`——语义 + BM25 + 正则一次融合,返回 `文件:行号` 可直接跳读。需安装 [zvec-grep](https://github.com/zvec-ai/zvec-grep) 并建过索引(`zg index`);未装则跳到下一通道;
2. **memU 语义检索(可选后端)**:`memu retrieve "问题" --json`——本地向量检索;需 memU 桥运行,未装或超时则退回文件协议;
3. **文件协议(永远可用)**:直读 `<知识库根>/INDEX.md` 定位条目,再读条目原文;精确关键词可用 grep/ripgrep 全库搜。

## 何时读

回答以下问题前,先查知识库(优先 zg / MCP `memory_search` / memU CLI):

- 用户的偏好、历史决策、项目背景、环境配置(硬件/服务/路径约定)
- "之前是不是做过/定过/讨论过……"
- 任何可能已有跨会话沉淀的上下文

## 何时写

会话中产生**可泛化**的知识(结论、决策、坑、外部资源指针)时:

1. 写入一条走技能 `kb-add`(查重后按协议落盘并登记 INDEX,收尾自动对账);或手工写 `<知识库根>/inbox/`,文件名 `<YYYY-MM-DD>-<主题-slug>.md`,带 frontmatter,并在 `INDEX.md` 顶部表格加指针行;
2. **手工落盘后必须对账索引**(否则检索命中的仍是旧快照):
   `python <仓库>/sync/sync_knowledge_base.py`(memU 轨道) + `cd <知识库根> && zg index`(zg 轨道);
3. **行为规则/规范类内容只能写 `norms/inbox-drafts/`**,等用户确认后升格——严禁直接写 `norms/` 生效区;
4. 全库体检与归位提议走技能 `kb-maintain`(只读审计)。

## 索引一致性(核心不变量)

KB 文件与检索索引(memU 向量库 / zg 索引)是两条独立写路径,**写入 KB 不会自动更新索引**。闭环三处:

- 写入技能收尾自动对账(`kb-add` 的 new/append 内置);
- Stop 钩子会话收尾兜底(仓库 `hooks/kb-stop-hook.mjs`,同时跑 memU 对账 + zg 刷新);
- 计划任务/登录脚本可再加一道 `sync_knowledge_base.py --if-changed`(幂等,带锁)。

两处自动触发都安全:锁防并发重复提交,指纹跳过无变动的情况。

## 边界(不可违反)

- 本库是**外置增量层**:不迁移、不删改任何 Agent 的本体记忆目录;
- 单一真值:同一事实与本库已确认条目冲突时,以本库已确认条目为准;
- 本契约的修改需用户确认;
- 隐私:知识库可能含个人环境信息,公开分享前先脱敏。
