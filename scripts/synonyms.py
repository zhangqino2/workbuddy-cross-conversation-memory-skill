"""Query expansion over a curated concept table.

The TF-IDF layer is bag-of-words: it cannot know that "包管理器" and "pnpm" are
related. Without help, a query phrased differently from the stored entry returns
nothing, which is the single most common way a memory system feels broken.

This module bridges that gap with a hand-curated table of concept groups drawn
from everyday engineering and office work. It is deliberately small and
conservative: a wrong expansion is worse than no expansion, so ambiguous terms are
dropped entirely rather than guessed at.

This is a heuristic, not semantics. It covers common phrasings; it will never
handle arbitrary paraphrase. The table is meant to be edited — add the vocabulary
that actually appears in your own work.
"""

from __future__ import annotations

import textproc

# Each group is one concept expressed several ways. Keep entries concrete:
# avoid generic words like 数据 / 系统 / 管理 / 问题, which appear everywhere and
# would expand every query into noise.
SYNONYM_GROUPS: list[list[str]] = [
    # --- tooling ---
    ["包管理器", "依赖安装", "pnpm", "npm", "yarn", "install", "lockfile"],
    ["构建工具", "打包", "webpack", "vite", "rollup", "esbuild", "build"],
    ["代码规范", "lint", "eslint", "prettier", "格式化", "风格检查"],
    ["版本控制", "git", "分支", "branch", "commit", "合并", "rebase"],
    ["测试", "单测", "test", "jest", "vitest", "pytest", "用例", "覆盖率"],
    ["编辑器", "ide", "vscode", "插件", "extension"],
    # --- data ---
    ["数据库", "postgres", "postgresql", "mysql", "sqlite", "mongodb", "db"],
    ["连接池", "连接数", "max_connections", "pool", "并发连接"],
    ["缓存", "redis", "cache", "ttl", "过期策略", "命中率"],
    ["消息队列", "kafka", "rabbitmq", "队列", "mq", "异步任务"],
    ["存储", "对象存储", "s3", "oss", "文件上传", "持久化"],
    ["数据清洗", "etl", "预处理", "去重", "规范化"],
    # --- infra ---
    ["部署", "上线", "发布", "deploy", "ci", "cd", "流水线", "pipeline"],
    ["容器", "docker", "k8s", "kubernetes", "镜像", "容器化"],
    ["监控", "告警", "prometheus", "grafana", "指标", "metrics"],
    ["日志", "log", "打点", "trace", "链路追踪"],
    ["配置", "config", "环境变量", "env", "配置文件"],
    ["性能", "优化", "慢", "延迟", "latency", "响应时间", "卡顿"],
    ["故障", "报错", "异常", "error", "bug", "崩溃", "失败", "挂了"],
    ["限流", "熔断", "降级", "rate limit", "过载保护"],
    # --- application ---
    ["登录", "登陆", "鉴权", "认证", "auth", "jwt", "session", "token", "cookie"],
    ["权限", "授权", "rbac", "角色", "acl", "访问控制"],
    ["接口", "api", "endpoint", "rest", "graphql", "路由"],
    ["前端框架", "react", "vue", "svelte", "angular"],
    ["状态管理", "redux", "pinia", "vuex", "zustand"],
    ["时间处理", "日期", "格式化", "dayjs", "moment", "时区"],
    ["表单", "校验", "validation", "输入验证"],
    ["爬虫", "抓取", "spider", "crawl", "采集"],
    ["搜索", "检索", "全文搜索", "elasticsearch", "索引"],
    # --- collaboration / process ---
    ["记忆", "memory", "上下文", "context", "跨会话"],
    ["偏好", "习惯", "倾向", "喜欢", "风格", "约定"],
    ["决策", "选型", "选择", "采用", "定下来", "方案对比"],
    ["教训", "踩坑", "复盘", "经验", "反思"],
    ["任务", "待办", "todo", "进度", "下一步"],
    ["文档", "说明", "readme", "注释", "手册"],
    ["会议", "讨论", "结论", "纪要"],
]

# A term claimed by more than this many groups is too generic to expand safely.
MAX_GROUPS_PER_TERM = 1
MAX_EXTRA_TOKENS = 28


def _group_terms(group: list[str]) -> set[str]:
    """Full words plus their retrieval tokens, lowercased."""
    terms: set[str] = set()
    for word in group:
        lowered = word.lower()
        terms.add(lowered)
        terms.update(textproc.tokenize(lowered))
    return terms


def _build_table() -> dict[str, set[str]]:
    ownership: dict[str, set[int]] = {}
    groups: list[set[str]] = [_group_terms(g) for g in SYNONYM_GROUPS]

    for idx, terms in enumerate(groups):
        for term in terms:
            ownership.setdefault(term, set()).add(idx)

    table: dict[str, set[str]] = {}
    for term, owners in ownership.items():
        if len(owners) > MAX_GROUPS_PER_TERM:
            continue  # ambiguous: skip rather than guess
        idx = next(iter(owners))
        table[term] = groups[idx] - {term}
    return table


EXPANSION: dict[str, set[str]] = _build_table()


def expand_tokens(tokens: list[str], max_extra: int = MAX_EXTRA_TOKENS) -> list[str]:
    """Return additional retrieval tokens implied by the query.

    Only tokens are returned (never whole phrases), because the index stores
    bigrams -- a phrase would never match.
    """
    if not tokens:
        return []
    seen = set(tokens)
    extra: list[str] = []
    for token in tokens:
        for word in EXPANSION.get(token, ()):
            for piece in textproc.tokenize(word):
                if piece in seen:
                    continue
                seen.add(piece)
                extra.append(piece)
                if len(extra) >= max_extra:
                    return extra
    return extra


def explain(query: str) -> list[tuple[str, list[str]]]:
    """Human-readable expansion trace, for debugging why a query matched."""
    tokens = textproc.tokenize(query)
    trace = []
    for token in tokens:
        words = sorted(EXPANSION.get(token, ()))
        if words:
            trace.append((token, words))
    return trace
