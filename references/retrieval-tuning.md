# Retrieval Tuning · 检索调优

**中文** — 当检索结果不对劲时读这篇：命中太少、命中不相关、或者你确定存在的记忆死活出不来。

**English** — Read this when search results feel wrong — too few hits, irrelevant hits, or
a memory you know exists fails to surface.

## The pipeline · 检索流程

```
query 查询
  │
  ├─ tokenize ──────────── ASCII 词/路径 + 中文字符 bigram
  │
  ├─ expand ────────────── 概念表扩展相关词（权重 0.45）
  │
  ├─ filter ────────────── kind / scope / tags / project / 过期
  │
  ├─ stage 1: BM25 ─────── 廉价宽召回（top 5×limit，至少 40）
  │
  ├─ stage 2: cosine ───── TF-IDF 稀疏向量，对候选重排
  │
  └─ fuse ──────────────── 加权融合 + 作用域加成
```

### Concept expansion · 概念扩展

**中文** — 词袋层无法知道"包管理器"和"pnpm"有关系，所以措辞与条目不同的查询会一无所获。
这种失败模式会让记忆系统显得完全坏掉，因此 `scripts/synonyms.py` 提供了一张人工维护的
概念组表来扩大粗召回阶段。

扩展词以 **0.45 权重**进入 BM25，且只作用于第一阶段。重排仍锚定用户实际输入，所以仅靠
扩展词命中的文档能被召回，但排在直接命中之后。

被多个概念组同时认领的词会被直接丢弃，而不是猜测——错的扩展比不扩展更糟。
`量子计算 硬件` 扩展结果为空，这是正确的。

这张表覆盖工具链、数据、基础设施、应用、协作五类词汇。它是**最值得按自己领域修改**的
文件：把你工作里真实出现的说法加进去。

```bash
python $MEM explain "包管理器怎么装的"
```

`explain` 会打印分词、扩展轨迹，以及每个命中的分项得分——在动任何权重之前先用它。

**English** — The bag-of-words layer cannot know that "包管理器" and "pnpm" are related, so
a query phrased differently from the stored entry would return nothing. Expansion tokens
enter BM25 at **0.45 weight**, and only in stage 1; reranking stays anchored to the user's
actual words. Terms claimed by more than one group are dropped rather than guessed at.
The table is the highest-leverage file to edit for your own domain.

### Why character bigrams for Chinese · 为什么中文用字符 bigram

**中文** — 不需要词典、不需要分词器、不引入依赖。"数据库迁移"会被索引成 `数据`、`据库`、
`库迁`、`迁移`。查"数据库"时，`数据` 和 `据库` 直接命中。它同时处理未知词、中英混排
和代码标识符。代价是比真正的分词器略多噪音——这由重排阶段吸收掉。

ASCII 词保留完整形式，同时也会被拆分，所以 `store.py` 会索引成 `store.py`、`store`、`py`。
查 "store" 因此能找到它。

**English** — No dictionary, no segmenter, no dependency. It handles unknown words, mixed
Chinese-English text, and code identifiers in the same pass. The cost is slightly noisier
matching than a real segmenter, which the rerank stage absorbs. ASCII tokens keep their
full form and also get split, so `store.py` indexes as `store.py`, `store`, and `py`.

### BM25

**中文** — 标准 Okapi BM25，`k1=1.5`、`b=0.75`。长度归一化在这里很重要，因为条目长度
从一行到整页不等。

归一化时除以 `max(候选最高分, 4.0)`。只按候选集最高分归一化会让小库里的弱匹配看起来
很强（两条记录的库永远有个"第一名"）；除以至少 4.0 能让信号保持诚实。

**English** — Standard Okapi BM25, `k1=1.5`, `b=0.75`. Length normalization matters here
because entries vary from one line to a full page. Normalizing by
`max(best_candidate, 4.0)` keeps weak matches honest when the candidate pool is small.

### Cosine rerank · 余弦重排

**中文** — TF-IDF 加权稀疏向量，L2 归一化，因此余弦就是点积。权重为 `(1 + log tf) × idf`。
这一步把"反复且独特地使用查询词"的文档，抬到"只提了一次"的文档之上。

**English** — TF-IDF weighted sparse vectors, L2-normalized, so cosine is a plain dot
product. Weight is `(1 + log tf) × idf`. This lifts a document that uses the query terms
repeatedly and distinctively above one that merely mentions them once.

### Fusion · 融合

**中文** — 只有对该查询真正生效的信号才会参与归一化，所以没有标签的查询不会因为没有
标签匹配而被扣分。

| 信号 Signal | 权重 Weight | 含义 Meaning |
|---|---|---|
| BM25 | 0.42 | 词面匹配强度 |
| Cosine | 0.26 | 词分布相似度 |
| Tag | 0.12 | 查询提到了条目的某个标签 |
| Recency | 0.10 | 指数衰减，半衰期按 `kind` 决定 |
| Importance | 0.10 | `(importance - 1) / 4` |

**中文** — 项目级条目有 ×1.12 加成，让本地上下文在势均力敌时胜出。

**English** — Project-scoped entries get a ×1.12 boost so local context wins ties against
global knowledge.

## Diagnosing bad results · 诊断结果不对

### "我确定它存在，但搜不到"

**中文** — 先用 `explain`，它会显示查询被转成了什么：

```bash
python $MEM explain "关键词"
python $MEM search "关键词" --limit 20 --include-expired
python $MEM list --limit 50            # 库里到底有没有？
python $MEM stats                      # 库选错了？项目级 vs 全局？
```

常见原因，按概率排序：

1. **库选错了。** 条目是 `scope=project`，而你在该项目外运行。进到项目里跑，或显式
   传 `--project-root`
2. **已过期。** 被 `supersedes` 或 `--expires` 退场了。用 `--include-expired` 查
3. **词面与概念都无重叠。** 如果 `explain` 显示 `no concept match`，而查询词和条目
   毫无交集，检索无法跨越这个鸿沟。把那个说法加进 `synonyms.py`，或改用条目标题里的词
4. **索引过期。** 少见，但 `python $MEM index` 永远安全且便宜

**English** — Start with `explain`. Most common causes, in order: wrong vault (project vs
global), expired entry, no lexical or concept overlap, stale index.

### "结果不相关"

**中文** — 通常是查询太泛——单个高频词，比如"配置"或"问题"。加一个区分项：

```bash
python $MEM search "配置" --kind decision --tags 数据库
python $MEM search "Docker 构建缓存" --limit 5
```

`--min-score 0.35` 也能砍掉长尾。分数大致归一化到 0-1，低于 0.3 的基本是噪音。

**English** — Usually the query is too generic. Add a discriminator, or use
`--min-score 0.35` to cut the tail. Scores are normalized to roughly 0-1, so anything
below ~0.3 is usually noise.

### "正确答案排在第二"

**中文** — 看 `--json` 输出里的 `signals` 块，它精确显示是哪个分量把错误条目推了上去：

```bash
python $MEM search "鉴权方案" --json --limit 3
```

- `recency` 高但 `bm25` 低 → 一条不重要的新条目在赢。提高对手的 `importance`，或降低
  错误条目的
- `tag` 高 → 像"后端"这种宽泛标签匹配过度。用更具体的标签
- `importance` 高 → 那条被标成 5 但不该是。**改数据，不要改算法**。排序是重要度取值的
  下游结果

**English** — Check the `signals` block in `--json` output. It shows exactly which
component pushed the wrong entry up. High `recency` but low `bm25` means an unimportant
recent entry is winning. High `importance` means the entry is marked 5 but should not be.
Fix the data, not the algorithm — ranking is downstream of honest importance values.

## Changing the weights · 调整权重

**中文** — 权重在 `scripts/retrieval.py` 顶部：

```python
W_BM25 = 0.42
W_COSINE = 0.26
W_TAG = 0.12
W_RECENCY = 0.10
W_IMPORTANCE = 0.10
```

| 症状 Symptom | 调整 Change |
|---|---|
| 旧但权威的记忆被埋没 | 提高 `W_IMPORTANCE`，降低 `W_RECENCY` |
| 新笔记应当占主导 | 提高 `W_RECENCY` |
| 精确关键词查询应当近乎确定 | `W_BM25` 提到 0.6 左右 |
| 换说法的查询总是漏 | 提高 `W_COSINE`；bigram 限制了它的上限 |
| 标签过滤不可靠 | 提高 `W_TAG`，并收紧标签词汇 |

**中文** — 其他可调项：BM25 长度敏感度的 `K1`/`B`、项目优先级的 `SCOPE_BOOST`、
`store.py` 里按类型区分的 `HALFLIFE_BY_KIND`。

**English** — Other knobs: `K1`/`B` for BM25 length sensitivity, `SCOPE_BOOST` for project
priority, and `HALFLIFE_BY_KIND` in `store.py` for per-kind decay.

## Performance · 性能

**中文** — 500 条实测：建索引约 0.7 秒，单次检索约 150 毫秒墙钟时间（含约 120 毫秒的
Python 解释器启动）。索引是单个 JSON 文件，只在源文件比它新、或库集合发生变化时重建。
线性扫描到 5000 条仍可交互；再往上考虑按年分片或迁到 SQLite FTS5。

**English** — Measured on 500 entries: index build ~0.7s, search ~150ms wall clock
(including ~120ms interpreter startup). Linear scanning to 5,000 entries stays
comfortably interactive; beyond that, consider sharding by year or moving to SQLite FTS5.

## Deliberate limits · 刻意的取舍

**中文**

- **没有词干还原。** 只有中文 bigram 和精确 ASCII 词。"running" 匹配不到 "run"
- **扩展是查表，不是语义。** `synonyms.py` 桥接常见说法，无法推断任意改写。概念重要时，
  在条目里把正式名和俗称都写上——这永远比依赖词表可靠
- **没有语义向量。** 真正的向量模型泛化更好，代价是 API 依赖和每次写入的成本。这里的
  余弦阶段与 BM25 共用同一个词面空间，所以它只锐化排序而不增加召回；增加召回的是扩展表
- **不做跨条目推理。** 检索返回条目，不返回综合后的答案。综合由调用方完成

**English**

- **No stemming.** Chinese bigrams and exact ASCII tokens only. "running" will not match "run".
- **Expansion is a lookup table, not semantics.** When a concept matters, put both its
  formal and colloquial names in the entry — that always beats relying on the table.
- **No semantic embedding.** The cosine stage operates on the same lexical space as BM25,
  so it sharpens ranking rather than adding recall; the expansion table is what adds recall.
- **No cross-entry reasoning.** Retrieval returns entries, not a synthesized answer.
