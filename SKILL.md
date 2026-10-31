---
name: cross-session-memory
description: 跨会话持久记忆。把事实、偏好、架构决策、经验教训、在办任务状态存为人类可读的 Markdown 文件，并通过关键词 + 向量混合检索召回。当用户说「记住这个／记一下／别忘了」，问「我们之前是不是说过／上次那个方案／之前定的什么」，需要从上一个会话恢复工作，或在做出决策、得到教训时使用。Durable memory across separate conversations and tasks: stores facts, user preferences, architectural decisions, lessons learned, and in-flight task state as human-readable Markdown, then recalls them by hybrid keyword + vector search. Use when the user says "remember this", asks "did we discuss this before / what did we decide last time", resumes work from a previous session, or when a decision, correction, or hard-won lesson is worth carrying forward.
agent_created: true
---

# Cross-Session Memory · 跨会话记忆

**中文** — 本地文件式记忆库，寿命长于单次对话。条目是带 YAML frontmatter 的纯
Markdown，存放在 `~/.workbuddy-ai/memory-vault/`（全局）和
`<project>/.workbuddy-ai/memory/`（项目级），用户可直接阅读、编辑、纳入版本控制。

真正让这套东西有用的只有两件事：**写出能独立成立的记忆**，以及**在该召回的时刻
主动召回，不必等人开口**。

**English** — A local, file-based memory store that outlives a single conversation.
Entries are plain Markdown with YAML frontmatter under `~/.workbuddy-ai/memory-vault/`
(global) and `<project>/.workbuddy-ai/memory/` (project-scoped), so the user can read,
edit, and version them directly.

Two things make this useful: **writing memories that stand alone**, and **recalling
them at the right moment without being asked**.

## Path setup · 路径设置

**中文** — 下文所有命令使用 `scripts/memory.py`，路径相对于本技能的根目录。每个会话
开始时把它解析成绝对路径：

```bash
MEM="<skill-base-dir>/scripts/memory.py"
```

只依赖 Python 3.9+ 标准库，无需安装，无需联网。

**English** — All commands below use `scripts/memory.py`, relative to this skill's base
directory. Resolve it to an absolute path once per session. Requires only Python 3.9+
with the standard library. No installs, no network.

## Recall · 召回：先查，再假设，再提问

**中文** — 只要既有上下文会改变你的回答，就先检索：

| 场景 | 命令 |
|---|---|
| 用户提到过去（"上次"、"之前"、"那个方案"） | `python $MEM search "<他的话>"` |
| 在某个项目里开始新任务 | `python $MEM digest --limit 10` |
| 准备选库／定模式／定表结构 | `python $MEM search "技术选型 <主题>"` |
| 用户问当初怎么定的、为什么 | `python $MEM search "<主题>" --with-body --limit 5` |
| 接着做没做完的活 | `python $MEM search "任务状态 <项目>" --kind task` |

读到结果再行动。**绝不要声称记得某个没有从检索结果里出现的东西**——如实说没找到记录，
然后问。

`digest` 是无查询预热：按重要度和时间返回价值最高的条目。会话刚开始、还不知道该搜
什么的时候用它。它很便宜（几百毫秒），宁可跑一次也别靠猜。

**English** — Run a search whenever prior context would change your answer. Read the
results, then act. Never claim to remember something that did not come back from a
search — say you found no record and ask.

`digest` is the no-query warm-up: it returns the highest-value entries by importance and
recency. Use it at the start of a session when you do not yet know what to search for.
It is cheap (a few hundred milliseconds), so prefer running it over guessing.

## Remember · 记忆：在事情定下来的那一刻写

**中文** — 一旦某件事变成持久信息，立刻写。不要攒到对话结束——那时准确措辞和理由
都已经丢了。

**该写：**
- 用户声明了偏好或约束（"我们用 pnpm"、"不要动 production"）
- 做出了决定，而且**理由**以后有用
- 出了问题，且修法不显然 → `kind=lesson`
- 你了解到关于他的环境、技术栈、组织的持久事实
- 任务没做完 → `kind=task`，好让下个会话接着干

**不该写：**
- 瞬时状态：临时路径、进程号、"正在跑测试"
- 一条命令就能从仓库里重新得到的东西
- 猜测——非要写就标 `--confidence low`
- 逐字对话记录。压缩成结论 + 理由。

**English** — Write as soon as something becomes durable. Do not batch memories to the
end of a conversation — by then the exact wording and rationale are gone.

### The quiet-by-default rule · 默认安静

**中文** — **多数会话应该产出零条记忆。一条算正常。三条以上说明你在记流水账，
而不是记结论。**

新建任务是**读**的时刻，不是写的时刻：跑 `digest` 或 `search`，然后干活。不要写
"任务已开始"这种条目——那是日记，日记会把真正重要的条目埋掉。只在事情**定下来**时写：
决定做了、偏好说了、故障根因找到了、教训换来了。

如果用户问记了什么，而答案是"没有"，这是正确且健康的结果——照实说，不要为了显得有用
而编造条目。

**English** — **Most sessions should produce zero memories. One is normal. Three or more
is a signal that you are logging activity instead of conclusions.**

Starting a new task is a *reading* moment, not a writing one. Do not write a "task
started" entry — that is a diary, and diaries bury the entries that matter. If the user
asks what was remembered and the answer is "nothing", say so plainly rather than
inventing entries to look useful.

```bash
python $MEM add \
  --title "构建统一用 pnpm，禁用 npm" \
  --kind preference \
  --tags "工具链,node" \
  --importance 4 \
  --content "所有 Node 项目使用 pnpm。原因：CI 缓存命中率更高，且 lockfile 冲突更少。不要生成 package-lock.json。"
```

### The self-containment rule · 自包含原则

**中文** — 记忆是在几个月后、被一个从没见过这场对话的 agent 读到的。每条都必须脱离
上下文就能读懂：

- 指名具体对象：文件路径、库名、接口、人、项目
- 同时写清决定**和**理由。没有理由的规则，第一次遇到不便就会被误用
- 不要用指向不明的代词：不写"改成那个方案了"，写"把鉴权从 session 改成 JWT"

反例：`用户说这样不行，改回原来的`
正例：`CI 上 Docker 构建缓存失效，改用 pnpm 的 store 缓存；npm ci 会忽略 store`

**English** — A memory is read months later, by an agent that never saw the conversation.
Every entry must be understandable in isolation. Name the concrete thing. State the
decision **and** the reason — a rule without a reason gets misapplied. Avoid pronouns
that point at nothing.

### Choosing `--kind` · 选择类型

| kind | 中文用途 | English |
|---|---|---|
| `fact` | 持久事实：技术栈、环境、组织结构、领域规则 | Durable truths |
| `preference` | 用户希望事情怎么做：风格、工具、约定 | How the user wants things done |
| `decision` | 一个选择及其理由；变更时取代旧条目 | A choice plus its rationale |
| `lesson` | 什么坏了、为什么；不显然的修法 | What broke and why |
| `entity` | 值得长期跟踪的人、项目、服务、产品 | A person, project, service, product |
| `task` | 必须活到下个会话的在办状态 | In-flight work state |
| `snippet` | 可复用的命令、配置块、代码片段 | A reusable command or config |

**中文** — `--importance`（1-5）直接影响排序，要诚实而不是慷慨：**5** = 忘了会出真
问题；**4** = 影响大部分后续工作；**3** = 有用的默认值；**2** = 次要；**1** = 琐事。

**English** — `--importance` (1-5) drives ranking, so be honest rather than generous.

### When a memory goes stale · 记忆过期时

**中文** — 不要堆积互相矛盾的条目。决定变了，就用新条目取代旧的：

```bash
python $MEM add --title "鉴权改用 JWT" --kind decision --importance 4 \
  --supersedes <old-id> --content "..."
```

旧条目会被自动置为过期并退出检索。对于确实临时的信息，用 `--expires 2026-11-30`
让它自己退场。

**English** — Do not pile up contradictions. If a decision changes, supersede the old
entry; it is expired automatically and drops out of search. For genuinely temporary
facts, set `--expires` so it self-retires.

## The write-then-search loop · 先查重再写入

**中文** — `add` 在写入前会做相似度检查，命中时拒绝写入并打印 `exists:`、`conflict:`
或 `similar:`。**把这行输出当作指令，而不是报错：**

- `exists:` — 已经记过，无需动作
- `conflict:` — 同标题但内容不同。要么 `update <id>` 修订，要么确认是独立条目后 `--force`
- `similar:` — 存在高度相似的条目。通常应该 `update` 它，而不是再加一条

这正是防止记忆库退化成"一堆近似笔记"的机制。

**English** — `add` runs a similarity check before writing. Treat `exists:` / `conflict:` /
`similar:` as an instruction, not an error. This is what keeps the store from degrading
into a pile of near-identical notes.

## Executable memories · 可执行记忆（脚本、CLI、操作手册）

**中文** — 这个库最有价值的用法，是记住**某个东西怎么跑**，好让下个会话不必重新摸索。
对于描述可运行物的 `snippet` 和 `task` 条目，一句话摘要毫无用处——要记操作细节：

```
脚本路径：~/scripts/qianmo-checkin.py
运行命令：python ~/scripts/qianmo-checkin.py --user <用户名>
依赖：requests；凭据在 ~/.qianmo.env
前置：需能访问 qianmo.example.com，公司网络要挂代理 127.0.0.1:7890
成功判据：输出含「签到成功」且退出码为 0
已知坑：
1. cookie 30 天过期，401 时重跑 --login
2. 每天 00:05 之后才计入当天
3. 重复签到返回「已签到」，不算失败，不要重试
```

上面每一行之所以存在，都是因为少了它就会出错或多跑一轮。标题用真实名称
（`阡陌论坛自动签到脚本`），别用 `脚本1` 这种标签——标题的检索权重最高。

召回这类条目时，先用 `get <id>` 拿全文再动手，检索摘要可能被截断；执行前确认路径还在。

**English** — The highest-value use of this store is remembering **how to run something**
so the next session does not rediscover it. Every line in such an entry exists because
its absence would cause a failure or a wasted round trip. Title it with the thing's real
name — the title carries the most retrieval weight. On recall, `get <id>` for the full
text before acting, then verify the path still exists.

**中文 — 传内容的正确方式**：shell 会改写 Windows 反斜杠路径，即使写在单引号里也一样
（`D:\magpie` 传进去会变成 `D:/magpie`）；多行长内容也很难引用。凡是路径密集或跨多行的
内容，先写进临时文件再用 `--file`，或用 `--stdin` 管道传入：

```bash
python $MEM add --title T --kind snippet --file /tmp/body.txt
python $MEM update <id> --file /tmp/body.txt
```

**边界**：这个库记的是**在哪、怎么跑**，它既不保存也不执行脚本。执行仍然交给常规
shell 工具，遵循与其他命令相同的确认规则。

**English — Passing content safely**: shells rewrite Windows backslash paths even inside
single quotes, and long multi-line content is awkward to quote. For anything path-heavy
or multi-line, use `--file` or `--stdin`. Note the boundary: this store remembers **where
and how**, it does not store or execute the script.

## Bootstrapping · 初始化：把已有的东西导入进来

**中文** — 新库是空的，而本技能从不自行扫描任何东西——自动收割只会把噪音灌满。当用户
希望把现有工作导入时，运行 `scan`：

```bash
python $MEM scan D:/workbuddy-ai              # 仅预览
python $MEM scan D:/workbuddy-ai --write      # 确认写入
```

它对每个子目录提出一条候选：真实项目（通过 README、构建清单、`.git` 或若干源码文件
识别）记为 `entity`；带时间戳的会话工作目录记为 `task`；空的或没内容的直接跳过。

扫描**在加 `--write` 之前全程只读**，且是幂等的——重复运行会跳过已存在的条目，所以
新增项目后再扫一次是安全的。加 `--update` 则刷新已有条目的正文而不改变 id。

自动生成的标题只是起点，不是定论。它们来自 README 的首句或最有描述性的产物名，通常
能用但寡淡。扫描之后，主动提议用 `update` 把重要的几条改得更准确。

扫描只能发现**磁盘上的项目**，读不了历史对话。要从旧对话里挖结论，用对话检索工具搜，
再用 `add` 把持久结论写进去。

**English** — A fresh store is empty, and the skill never scans anything on its own —
automatic harvesting would flood the store with noise. `scan` proposes one entry per
child directory and is **read-only until `--write`**. It is idempotent, so it is safe to
repeat after adding projects; `--update` refreshes existing entries in place. Scan finds
projects on disk — it cannot read past conversations.

## Command reference · 命令速查

```bash
python $MEM add --title T --kind K --tags a,b --importance N --content C
python $MEM search "query" [--with-body] [--limit N] [--kind K] [--scope global|project]
python $MEM get <id>                  # 全文 / full entry
python $MEM list [--kind K] [--sort importance]
python $MEM update <id> [--title/--content/--tags/--importance/--expires]
python $MEM forget <id>...            # 移入 .trash，可恢复 / recoverable
python $MEM digest [--limit N]        # 无查询取高价值条目
python $MEM scan <dir> [--write]      # 导入现有项目
python $MEM explain "query"           # 诊断为什么命中/没命中
python $MEM stats                     # 统计与库位置
python $MEM prune                     # 清理过期条目与旧回收站
python $MEM export --format md|json   # 全量导出，便于审阅或迁移
```

**中文** — 任何命令加 `--json` 可得机器可读输出。`add` 加 `--scope project` 可把记忆
留在当前项目而非全局库。

如果一次本该成功的检索返回空，先用 `explain` 再下结论说记忆不存在——它会显示分词、
概念扩展和分数构成，通常能暴露出库选错了或措辞有缺口。

**English** — Add `--json` to any command for machine-readable output. Add `--scope
project` to keep a memory inside the current project. If a search that should have
worked returns nothing, run `explain` before concluding the memory is absent.

## Scope discipline · 作用域

**中文** — 默认全局。只有当某条记忆仅对某一个仓库成立时才用 `--scope project`——端口号、
本地表结构、迁移计划。项目级记忆在该项目内会被自动检索，在其他地方不可见，从而避免污染
无关的工作。

**English** — Global by default. Use `--scope project` when the memory is only true for
one repository. Project memories are searched automatically inside that project and stay
invisible elsewhere.

## Resources · 参考文档

- `references/writing-rules.md` — 什么让一条记忆有用、什么让它误导，配正反例。
- `references/entry-schema.md` — 每个 frontmatter 字段的类型与对检索的影响。
- `references/retrieval-tuning.md` — 打分原理，以及结果不对时怎么调。
