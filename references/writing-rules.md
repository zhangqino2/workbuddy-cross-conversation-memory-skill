# Writing Rules · 写入准则

**中文** — 检索层很快，但决定这套系统成败的是写入层。一堆含糊的条目比空库更糟，
因为它会产出"自信的错误答案"。

**English** — The retrieval layer is fast; the writing layer is where memory systems
succeed or fail. A store full of vague entries is worse than an empty one, because it
produces confident wrong answers.

## Rule 1 · 自包含，否则毫无价值

**中文** — 条目是在几个月后被一个零上下文的 agent 读到的。要为那个读者写。

| 差 | 为什么失败 | 好 |
|---|---|---|
| `用户说这样不行，改回原来的` | "这样"和"原来的"指向不明 | `CI 上 Docker 层缓存失效，回退到 pnpm store 挂载方案` |
| `用那个库` | 没说是哪个库 | `日期处理统一用 dayjs，不用 moment（包体积 2KB vs 290KB）` |
| `数据库配置改好了` | 哪个库、哪个值、哪个环境 | `生产库连接池上限设为 20，超过会触发 PG 的 max_connections 限制` |
| `修复了登录 bug` | 一周后无从复现 | `登录态丢失是因为 JWT 放在 sessionStorage，刷新标签页后被清空；改用 httpOnly cookie` |

检验方法：把这条粘进一个没有任何上下文的新对话。一个称职的工程师能照着行动吗？
不能就重写。

**English** — An entry is read months later by an agent with zero context. Write for that
reader. Test: paste the entry into a fresh conversation. Can a competent engineer act on
it? If not, rewrite it.

## Rule 2 · 记理由，不只记规则

**中文** — 没有理由的规则，第一次遇到不便就会被误用。

差：`不要用 Redis 做队列`
好：`不要用 Redis List 做任务队列。2026-10 因为 BRPOP 在网络分区时丢消息，导致 3 笔订单
未发货。改用 Kafka，至少一次投递 + 幂等消费。`

理由告诉下一个 agent：这条规则什么时候仍然成立，什么时候已经过期。

**English** — A rule without a reason gets misapplied the first time it is inconvenient.
The reason tells the next agent when the rule still applies and when it has expired.

## Rule 3 · 一条记忆只承载一个主张

**中文** — 拆分不是形式主义，而是检索能工作的前提。一条混了五个事实的条目，对每个查询
都只有弱匹配，没有一个强匹配。

差：
```
项目用 Vue3 + Vite + Pinia，部署在 K8s，CI 用 GitHub Actions，
数据库是 Postgres 15，缓存用 Redis 7，日志走 Loki。
```

好：拆成六条，各有自己的标签。这样 `search "缓存"` 只返回一条高置信命中，而不是一堵
无关基础设施的墙。

例外：紧密耦合、只有合在一起才有意义的事实应当合并——比如一个表结构决策和它的迁移方案
属于同一条 `decision`。

**English** — Splitting is not bureaucracy — it is what makes retrieval work. A single
entry mixing five facts matches every query weakly and none strongly. Exception: keep
tightly coupled facts together when they only make sense jointly.

## Rule 4 · 在事情定下来的那一刻写

**中文** — 记忆质量随延迟衰减。准确措辞、被否决的备选方案、以及理由，都会在几个回合内
模糊掉。

以下情况立即写：
- 用户纠正了你。**纠正是价值最高的记忆**。
- 经过真实权衡后做出了选择。
- 某处坏了，而修法并不显然。

不要等"对话结束"——那个时刻经常根本不存在。

**English** — Memory quality decays with delay. Write immediately when the user corrects
you (corrections are the highest-value memories that exist), when a choice is made after
real deliberation, or when something breaks and the fix was not obvious. Do not wait for
"the end of the conversation" — there is often no such moment.

## Rule 5 · 不要记录能重新推导的东西

**中文** — 一条命令就能复现的，不该进记忆：

- 当前 git 分支、`src/` 下的文件列表、已安装的依赖树
- `README.md`、`package.json`、schema 文件里的任何内容
- 你随手就能读到的文件内容

记忆的价值在于承载**任何地方都没写下来的东西**：某个选择背后的理由、没人记录过的约束、
上次坏在哪里。

**English** — If one command reproduces it, it does not belong in memory. Memory earns its
place by holding what is **not written down anywhere**: the reason behind a choice, the
constraint nobody documented, the thing that broke last time.

## Rule 6 · 尊重用户对确定性的表述

**中文** — 用户在探索阶段，不要当成决定记下来。

- "要不试试 Redis?" → 还不是记忆
- "就定 Redis 了" → `kind=decision`

当你是在推断而非被告知时，标 `--confidence low`。低置信条目仍可被检索到，但呈现时应
说明它是推测。

**English** — If the user is exploring, do not record it as a decision. When inferring
rather than being told, set `--confidence low`. Low-confidence entries are still
retrievable but should be presented as tentative.

## Rule 7 · 主动清理

**中文** — 只增不减的记忆库会变成噪音。三种机制，按优先级：

1. **`--supersedes <id>`** — 旧决定被取代时自动退场
2. **`--expires YYYY-MM-DD`** — 有明确保质期的事实（临时端口、待完成的迁移）
3. **`forget <id>`** — 事后发现记错了。会移入 `.trash`，错误可恢复

偶尔跑一次 `prune`，清掉过期条目和旧回收站。

**English** — A memory store that only grows becomes noise. Three mechanisms, in order of
preference: `--supersedes`, `--expires`, `forget`. Run `prune` occasionally to clear
expired entries and old trash.

## Anti-patterns · 实践中常见的反面模式

**中文**

**日记体。** 记录每一轮对话（"用户问了 X，我答了 Y"）。没人会去检索这个，而它会把
真正的记忆淹掉。记结论，不记活动。

**神谕体。** 把推断写成事实（"用户应该喜欢 TypeScript"）。没观察到的事，不要断言。

**复读机。** 每个会话都加一条略微改写的条目。CLI 会挡住明显的情况，但习惯才是问题所在：
先检索，再 `update`。

**孤儿条目。** 引用了某个 ID、工单或文件，却不说明它是什么，而且那东西已经不存在了。
永远把指代对象写全。

**English** — *The diary*: recording every turn. Nobody searches this, and it drowns the
real memories. *The oracle*: writing inferences as facts. *The duplicate*: adding a
slightly reworded entry each session. *The orphan*: referencing an ID or file with no
explanation of what it was. Always spell out the referent.

## A worked example · 一个完整示例

**中文** — 对话片段：

> 用户：这个接口太慢了，3 秒。
> Agent：看了下是 N+1 查询，orders 循环里查 user。
> 用户：那就 batch 一下吧。
> Agent：改成一次性 IN 查询 + 内存 map，现在 120ms。

弱记忆：
```
title: 接口优化了
body: 把 N+1 改成 batch 查询，快了很多。
```

强记忆：
```bash
python $MEM add \
  --title "订单列表接口 N+1 查询已改为批量 IN 查询" \
  --kind lesson \
  --tags "性能,数据库,N+1" \
  --importance 4 \
  --content "GET /api/orders 原实现每行查一次 user，200 条订单耗时 3s。
改为一次 IN 查询 + 内存 map 后 120ms。以后写列表接口先看有没有循环内查询：
用 EXPLAIN 或 ORM 的 eager loading 检查。"
```

强版本点名了接口、机制、数字，以及可推广的规则。它能匹配未来"接口慢"或"N+1"的查询，
并告诉下一个 agent 该检查什么。

**English** — The weak version names nothing and will never match a useful query. The
strong version names the endpoint, the mechanism, the numbers, and the generalizable rule.
It will match a future query like "接口慢" or "N+1", and it tells the next agent what to
check.
