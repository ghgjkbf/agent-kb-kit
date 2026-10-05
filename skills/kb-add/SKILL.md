---
name: kb-add
description: 用户说记住/存知识库/记一笔，或会话产出可泛化结论、决策、坑、外部资源指针时自动触发：查重后按 KB 协议写入一条并登记 INDEX。
---

# kb-add — 知识库快速添加（写一条）

共享知识库根目录由 `KB_ROOT` 指定（默认 `<仓库>/knowledge-base`，配置见仓库 README），读写协议全文见该目录 `README.md`（权威真值，动手前先读）。本技能只管**写入一条**：全库整理走 `kb-maintain`，读取走 `knowledge-base`。

## 触发

- 用户说「记住这个 / 存进知识库 / 记一笔 / 存一下」
- 会话产出**可泛化**的结论、决策、踩坑、外部资源指针、环境事实
- 门槛：跨会话或跨 Agent 有用才写；一次性噪音、通用常识、临时中间态不写

## 流程

1. **定类型**：`fact` 稳定事实 / `project` 项目上下文 / `conversation` 对话沉淀 / `reference` 外部资源指针 / `norm` 行为规范。再给 `confidence`（high/medium/low）。
2. **查重（硬步骤，不许跳过）**：

   ```
   python <本技能目录>/scripts/kb_add.py check --query "关键词"
   ```

   它跑 memU 语义检索（未装则自动只做文本回找），并在 `INDEX.md` 与文件名里回找。命中同主题 → 改**原条目**并追加更新记录（`kb_add.py append`），**不新建**文件。
3. **写入**：

   ```
   python <本技能目录>/scripts/kb_add.py new --title "标题" --type fact --agent <写入者> --body <正文文件|-> [--confidence high] [--slug 英文短名]
   ```

   落点 `inbox\<日期>-<slug>.md`；`type: norm` 强制落 `norms\inbox-drafts\`。脚本负责 frontmatter（date/agent/type/confidence/status/title）、文件名 slug、在 `INDEX.md` 表格首行插入指针。
4. **对账**：脚本默认收尾跑 `sync-knowledge-base.py --if-changed`（KB→memU 对账），并顺手 `zg index` 刷新 zvec-grep 索引（zg 未装或失败则跳过，不影响写入；增量约 1 秒）。手动写盘不跑对账，`memu retrieve` / `zg query` 命中的就还是旧快照（2026-10-03 实测的脱节根因）。
5. **回报**：给出条目的绝对路径 + 一句结论，不要复述全文。

## 脚本

`scripts\kb_add.py`，纯标准库。源码刻意全 ASCII：个别 Windows 环境的 `python` 按 ANSI 读源码，非 ASCII 字符串只从参数/stdin 进来、按 UTF-8 写回。配置全部走环境变量（`KB_ROOT` / `MEMU_EXE` / `KB_SYNC` / `KB_SYNC_PY` / `ZG_EXE`），缺省自动探测，见文件头 docstring。

| 命令 | 用途 |
|---|---|
| `check --query Q` | 查重：memU 语义检索 + INDEX/文件名文本匹配 |
| `new --title T --type T --agent A [--confidence C] [--slug S] [--body F] [--dry-run] [--no-sync] [--force]` | 新建条目并登记 INDEX |
| `append --path P --note N` | 给已有条目追加「更新记录」 |
| `sync` | 单独跑 KB→memU 对账 + zg 索引刷新 |

`--body -` 从 stdin 读正文。`--dry-run` 只打印将写入的文件名与 INDEX 行，不落盘。

## 边界

- 规范类（norm）只能写 `norms\inbox-drafts\`；升格到 `norms\` 生效区必须用户确认（README 契约）。
- 同一事实与本库已确认条目冲突时以本库为准；新结论要写就标注取代关系，不静默覆盖。
- 一律使用用户约定的真实路径；不要把条目内容写到临时目录或兼容层路径。
- 本技能不删条目；过期处理交 `kb-maintain`。
