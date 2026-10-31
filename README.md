# cross-session-memory

**跨会话记忆技能，为 AI agent 提供可长期存活的本地记忆库。**
A durable, file-based memory skill for AI agents.

把事实、偏好、架构决策、经验教训、在办任务状态存为人类可读的 Markdown 文件，
下次对话用一条命令就能召回。零依赖、零网络、可手改、可 git 版本化。

Stores facts, preferences, architectural decisions, lessons learned, and in-flight task
state as human-readable Markdown — then recalls them with one command. No dependencies,
no network, no lock-in.

---

## 为什么

Agent 每次会话都是失忆的。上下文窗口关掉，上次定好的方案、你说过的偏好、踩过的坑，
全部清零。多数"记忆"方案要么绑在某个平台上，要么塞进一个你看不见的数据库。

这个技能选择另一条路：**一条记忆 = 一个 Markdown 文件**。你能打开它、改它、删它、
把它放进 git。记忆库不该是黑盒。

## 特性

- **纯本地** — 只依赖 Python 3.9+ 标准库，无安装、无 API key、无网络
- **人可读** — Markdown + YAML frontmatter，随时手改
- **混合检索** — 中文 bigram 分词 → 概念扩展 → BM25 粗筛 → TF-IDF 余弦精排
- **会遗忘** — 按类型设定衰减半衰期，`task` 30 天淡出，`preference` 540 天常驻
- **防退化** — 写入前查重，拒绝重复和高度相似条目
- **可审计** — `list` / `stats` / `export` 随时查看库里到底有什么
- **导入存量** — `scan` 一条命令把现有项目沉淀进库
- **中英双语文档**

## 安装

克隆到技能目录：

```bash
git clone https://github.com/zhangqino2/cross-session-memory.git \
  ~/.workbuddy-ai/skills/cross-session-memory
```

新开一个对话即可生效。技能的 `description` 会告诉 agent 何时调用它。

## 快速开始

```bash
MEM=~/.workbuddy-ai/skills/cross-session-memory/scripts/memory.py

# 记一条
python $MEM add --title "构建统一用 pnpm，禁用 npm" \
  --kind preference --tags "工具链,node" --importance 4 \
  --content "所有 Node 项目使用 pnpm。原因：CI 缓存命中率高，lockfile 冲突少。"

# 召回（措辞不必和条目一致）
python $MEM search "装依赖用哪个工具" --with-body

# 没有查询时预热上下文
python $MEM digest --limit 10
```

## 命令

| 命令 | 作用 |
|---|---|
| `add` | 写入一条记忆（内置查重） |
| `search` | 检索，支持 `--kind` / `--tags` / `--scope` 过滤 |
| `get <id>` | 读取单条全文 |
| `list` | 列出条目，可按时间或重要度排序 |
| `update <id>` | 修订，支持 `--file` / `--stdin` |
| `forget <id>` | 删除（移入 `.trash`，可恢复） |
| `digest` | 无查询返回最高价值条目 |
| `scan <dir>` | 扫描目录，导入现有项目 |
| `explain "q"` | 诊断为什么命中／没命中 |
| `stats` | 统计与库位置 |
| `prune` | 清理过期条目与旧回收站 |
| `export` | 全量导出 md 或 json |

任何命令加 `--json` 可得机器可读输出。

## 记忆类型

| kind | 半衰期 | 用途 |
|---|---|---|
| `preference` | 540 天 | 工作习惯、约定 |
| `decision` | 360 天 | 带理由的选择 |
| `lesson` | 360 天 | 踩坑与复盘 |
| `fact` | 270 天 | 环境与领域事实 |
| `entity` | 270 天 | 人、项目、服务 |
| `snippet` | 180 天 | 命令、配置 |
| `task` | 30 天 | 在办状态 |

选对类型比选对重要度更影响排序——这是刻意设计：旧的教训仍然值钱，旧的任务状态应该消失。

## 存储位置

```
~/.workbuddy-ai/memory-vault/        # 全局，跨项目
<project>/.workbuddy-ai/memory/      # 项目级，仅在该项目内可见
```

## 设计取舍

- **不自动扫描。** 装好后什么都不碰。自动收割只会把噪音灌满，让真正重要的记忆沉底
- **默认安静。** 多数会话应该产出零条记忆。新建任务是读的时刻，不是写的时刻
- **不做语义向量。** 用概念表桥接常见说法，避免 API 依赖和每次写入的成本
- **只记"在哪、怎么跑"，不执行脚本。** 执行仍交给常规 shell，遵循同样的确认规则

更多细节见 `references/`：

- `writing-rules.md` — 什么让一条记忆有用，配正反例
- `entry-schema.md` — 每个字段的类型与对检索的影响
- `retrieval-tuning.md` — 打分原理与调参

## 要求

Python 3.9+。仅此而已。

## License

MIT
