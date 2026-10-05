// kb-stop-hook.mjs — Stop 钩子:会话收尾时提醒一次知识库沉淀 + 后台对账 KB/memU 索引 + 刷新 zg 索引
// 适用于 ZCode / Claude Code 等支持 Stop hook 的 Agent(接线见仓库 README)。
// 每次会话(session_id)最多阻断提醒一次。所有后端都是可选:缺 memU / 缺 zg 时静默降级。
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const REPO = dirname(dirname(fileURLToPath(import.meta.url))); // 本文件位于 <repo>/hooks/
const KB = process.env.KB_ROOT || join(REPO, 'knowledge-base');
const PY = process.env.KB_PYTHON || 'python'; // 同步脚本仅用标准库,PATH 上的 python 即可
const SYNC = process.env.KB_SYNC || join(REPO, 'sync', 'sync_knowledge_base.py');
const ZG = process.env.ZG_EXE || 'zg';
const HAS_SYNC = existsSync(SYNC);

// 后台对账:KB 文件落盘后 memU 向量库不会自动感知(retrieve 命中的是旧快照)。
// detached + stdio ignore:不阻塞钩子、不占用 stdin/stdout;脚本自带锁与指纹跳过,重复调用安全。
function reconcile() {
  if (!HAS_SYNC) return; // 未随仓库安装 sync 脚本时跳过 memU 轨道
  try {
    const child = spawn(PY, [SYNC, '--if-changed', '--quiet'], {
      cwd: KB, detached: true, stdio: 'ignore', windowsHide: true,
      env: { ...process.env, KB_ROOT: KB },
    });
    child.on('error', () => {}); // python 不可用时不阻塞会话收尾
    child.unref();
  } catch { /* 桥或 python 不可用时不阻塞会话收尾 */ }
}

// zg(zvec-grep)索引刷新:与 memU 对账同性质,写入侧不会自动感知,收尾补一次(增量约 1 秒)。
// shell:true 让 Windows 能从 PATH 解析 zg.cmd;未装 zg 或刷新失败都不阻塞会话收尾。
function refreshZg() {
  try {
    const child = spawn(ZG, ['index'], {
      cwd: KB, detached: true, stdio: 'ignore', windowsHide: true,
      shell: process.platform === 'win32',
    });
    child.on('error', () => {});
    child.unref();
  } catch { /* zg 不可用时不阻塞会话收尾 */ }
}

// 消费 stdin(容错:3 秒读不到就继续,ZCode 关闭 stdin 前 hook 不依赖其内容)
let raw = '';
try {
  raw = await Promise.race([
    (async () => { let s = ''; for await (const c of process.stdin) s += c; return s; })(),
    new Promise((r) => setTimeout(() => r(''), 3000)),
  ]);
  JSON.parse(raw || '{}'); // 仅校验,不使用;非 JSON 也容忍
} catch { /* stdin 非 JSON 不影响提醒 */ }

// 每次会话收尾都跑一次索引对账(不依赖 session_id,与本会话是否已提醒过无关)
reconcile();
refreshZg();

const sid = process.env.CLAUDE_SESSION_ID || '';
if (!sid) process.exit(0); // 无会话标识绝不阻断,防止全局误伤

const seenDir = join(KB, '.hooks', 'seen');
const marker = join(seenDir, `${sid}.flag`);
if (existsSync(marker)) process.exit(0); // 本会话已提醒过,放行

mkdirSync(seenDir, { recursive: true });
writeFileSync(marker, new Date().toISOString(), 'utf8');

process.stdout.write(JSON.stringify({
  decision: 'block',
  reason: '会话收尾检查(每次会话仅提醒一次):若本次对话产生了可泛化知识(用户决策、环境事实、项目坑、外部资源指针),请按知识库 README(写入协议)写一条 inbox/ 条目(带 frontmatter)并更新 INDEX.md;行为规则/规范类内容只能写 norms/inbox-drafts/ 草案;确无可沉淀内容则直接正常结束,不要重复本检查。',
}));
