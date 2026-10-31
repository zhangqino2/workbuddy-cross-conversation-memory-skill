"""Text processing for cross-session memory retrieval.

Pure standard library. Chinese is indexed with character bigrams (the classic
CJK approach used by Lucene's CJKAnalyzer): it needs no dictionary, survives
unknown words and mixed-language text, and gives solid recall for short
memory entries.
"""

from __future__ import annotations

import re
import unicodedata

# --- Stopwords -------------------------------------------------------------

# Single characters that carry no retrieval signal on their own.
CJK_STOP_CHARS = set(
    "的了吗呢吧啊呀哦嗯是在和有就都也很到说要去你他会着看好这那么些个上"
    "下中大小多不了过还没被把让给对从向以为与及或但而则之其此该每各"
)

# Bigrams that are grammatical glue rather than content.
CJK_STOP_BIGRAMS = {
    "的是", "了的", "这个", "那个", "我们", "你们", "他们", "她们", "它们",
    "可以", "如果", "因为", "所以", "但是", "然后", "以及", "或者", "不是",
    "没有", "一个", "什么", "怎么", "这样", "那样", "就是", "还是", "已经",
    "现在", "应该", "需要", "进行", "通过", "使用", "对于", "关于", "由于",
    "虽然", "而且", "并且", "不过", "只是", "这些", "那些", "一些", "一下",
    "可能", "一定", "非常", "比较", "更加", "很多", "所有", "任何", "每个",
    "其他", "其它", "之后", "之前", "时候", "问题", "情况", "方面", "地方",
    "东西", "事情", "方式", "方法", "这里", "那里", "其中", "另外", "同时",
    "于是", "然而", "并且", "不但", "而是", "不是", "不要", "不能", "不会",
}

EN_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "is", "are", "was", "were", "be", "been", "being", "to",
    "of", "in", "on", "at", "for", "with", "by", "from", "as", "it", "its",
    "we", "you", "they", "he", "she", "i", "me", "my", "our", "your", "their",
    "do", "does", "did", "have", "has", "had", "will", "would", "can", "could",
    "should", "may", "might", "must", "not", "no", "yes", "so", "such", "there",
    "here", "when", "where", "what", "which", "who", "how", "why", "all", "any",
    "some", "more", "most", "other", "into", "out", "up", "down", "about", "over",
    "also", "just", "only", "very", "much", "many", "one", "two", "use", "used",
}

STOPWORDS = EN_STOPWORDS | CJK_STOP_BIGRAMS

# --- Patterns --------------------------------------------------------------

_CJK_RANGE = "\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
CJK_RUN_RE = re.compile(f"[{_CJK_RANGE}]+")
# Identifiers, paths, versions, snake_case, dotted names, code tokens.
ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_@#$][A-Za-z0-9_@#$\-./\\:+]*[A-Za-z0-9_]|[A-Za-z0-9]")
SPLIT_RE = re.compile(r"[/\\._:+\-]+")


def normalize(text: str) -> str:
    """NFKC-normalize and lowercase for stable matching."""
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text).lower()


def tokenize(text: str, ngram: int = 2) -> list[str]:
    """Return retrieval tokens: ASCII words/paths plus CJK character n-grams."""
    text = normalize(text)
    if not text:
        return []

    tokens: list[str] = []

    for match in ASCII_TOKEN_RE.finditer(text):
        word = match.group().strip(".-/\\:_+")
        if len(word) < 2 or word in STOPWORDS:
            continue
        tokens.append(word)
        # Index the parts of compound identifiers so `store.py` matches `store`.
        if any(ch in word for ch in "/\\_.:-+"):
            for part in SPLIT_RE.split(word):
                if len(part) >= 2 and part != word and part not in STOPWORDS:
                    tokens.append(part)

    for match in CJK_RUN_RE.finditer(text):
        run = match.group()
        if len(run) == 1:
            if run not in CJK_STOP_CHARS:
                tokens.append(run)
            continue
        for i in range(len(run) - ngram + 1):
            gram = run[i : i + ngram]
            if gram in STOPWORDS:
                continue
            if gram[0] in CJK_STOP_CHARS and gram[1] in CJK_STOP_CHARS:
                continue
            tokens.append(gram)

    return tokens


def term_freq(tokens: list[str]) -> dict[str, int]:
    """Count token occurrences."""
    tf: dict[str, int] = {}
    for token in tokens:
        tf[token] = tf.get(token, 0) + 1
    return tf


def extract_tags_from_query(query: str, known_tags: set[str]) -> list[str]:
    """Find known tags mentioned inside a free-form query."""
    if not known_tags:
        return []
    q = normalize(query)
    hits = []
    for tag in known_tags:
        tag_n = normalize(tag)
        if tag_n and tag_n in q:
            hits.append(tag)
    # Prefer the longest match so `跨对话记忆` wins over `记忆`.
    hits.sort(key=len, reverse=True)
    return hits
