"""Reviewed platform operations available to the account's AI assistant.

This is a capability catalog, not a second implementation of platform business
logic. The executor must call the existing HTTP route with the caller's verified
identity, preserving ownership checks, membership gates, quotas and validation.
New API routes are closed by default: only exact method/path pairs reviewed here
are eligible. Schemas are read after app registration, never at import time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapabilityRule:
    group: str
    title: str
    effect: str = "read"
    shared: bool = False
    required_role: str = "user"


# These exact route templates were reviewed against main.py, portfolio_api.py
# and webhook_push.py. Do not replace this table with prefix matching or expose
# every newly registered OpenAPI operation automatically.
_REVIEWED: dict[tuple[str, str], CapabilityRule] = {}


def _group(group: str, entries: str, *, shared: bool = False) -> None:
    for line in entries.strip().splitlines():
        method_path, title, *effect_parts = (part.strip() for part in line.split("|"))
        method, path = method_path.split(" ", 1)
        effect = effect_parts[0] if effect_parts else "read"
        if method == "DELETE" or (shared and effect != "read"):
            effect = "confirm"
        _REVIEWED[(method, path)] = CapabilityRule(
            group, title, effect, shared,
            "admin" if shared and effect != "read" else "user",
        )


_group("account", """
GET /api/auth/me | 查看我的账号和会员状态
GET /api/auth/invite | 查看我的邀请信息
GET /api/me/referral | 查看我的邀请奖励
GET /api/referral/leaderboard | 查看邀请排行榜
GET /api/referral/campaign | 查看邀请活动
POST /api/me/referral/redeem | 兑换我的邀请奖励 | confirm
POST /api/membership/claim-trial | 领取我的体验会员 | write
POST /api/membership/redeem | 用兑换码开通我的会员 | confirm
GET /api/payment-config | 查看会员套餐价格
GET /api/community/group | 查看交流群入口
GET /api/support/thread | 查看我的客服会话
GET /api/support/unread | 查看我的客服未读消息
POST /api/support/send | 给客服发送消息 | confirm
GET /api/me/bookmarks | 查看我的收藏
POST /api/me/bookmark | 收藏或取消收藏资讯 | write
POST /api/news/react | 对资讯表达看多或看空 | write
POST /api/news/reactions | 查看资讯的表态统计
""")

_group("watchlist", """
GET /api/me/watchlist | 查看我的自选股
POST /api/me/watchlist | 保存我的自选股 | write
GET /api/watchlist/analysis | 分析我的自选股纪律
""")

_group("market", """
GET /api/market/quotes | 查询实时行情
GET /api/market/search | 搜索股票名称和代码
GET /api/market/data-layers | 查看行情数据覆盖
GET /api/market/ashare/structured | 查看 A 股结构化数据
GET /api/market/kline | 查看个股 K 线
GET /api/themes/boards | 查看题材板块涨幅榜
GET /api/themes/detail | 查看题材成分股
GET /api/themes/stock | 查询个股所属题材
GET /api/themes/limit-up | 查看涨停天梯
GET /api/options/signals | 查看期权信号
POST /api/options/ai-analysis | 分析期权信号
GET /api/earnings/calendar | 查看财报日历
POST /api/decision/multi-market | 分析跨市场决策
GET /api/agents/premarket-opportunities | 查看盘前机会
GET /api/stock/tear-sheet | 查看个股速判卡
GET /api/stock/dragon-tiger | 查看个股龙虎榜
GET /api/stock/consensus | 查看分析师一致预期
GET /api/stock/dividends | 查看个股分红记录
GET /api/stock/news | 查看个股新闻
GET /api/stock/risk-check | 检查个股风险
GET /api/stock/compare | 横向比较股票
GET /api/stock/screen | 按条件筛选股票
GET /api/market-dashboard | 查看跨市场大盘指标
GET /api/market-dashboard/ashare | 查看 A 股大盘指标
GET /api/market-risk-radar | 查看市场风险雷达
POST /api/market-dashboard/analyze | 分析跨市场大盘
POST /api/market-dashboard/ashare/analyze | 分析 A 股大盘
GET /api/dragon-tiger/daily | 查看每日龙虎榜
GET /api/calendar/cn | 查看 A 股事件日历
GET /api/ifind/status | 查看我的增强行情资格
GET /api/ifind/quote | 查询增强行情
""")

_group("content", """
GET /api/messages/by-symbol | 查询个股相关资讯
GET /api/realtime/messages | 检索平台快讯和文章
GET /api/realtime/messages/{message_id} | 查看一条平台资讯
GET /api/headlines | 查看今日头条
GET /api/official-news/cctv | 查看官方新闻
GET /api/people/spotlight | 查看人物专题
GET /api/people/{figure_id}/digest | 查看人物观点摘要
GET /api/celebrity/views | 查看授权名人观点
GET /api/celebrity/{celeb_id}/digest | 查看授权名人观点摘要
GET /api/celebrity/{celeb_id}/more | 查看授权名人的历史观点
GET /api/celebrity/{celeb_id}/comments | 查看授权名人观点评论
GET /api/zsxq/stream | 检索机构纪要
GET /api/zsxq/topic-comments | 查看机构纪要评论
GET /api/zsxq/sentiment-summary | 查看机构纪要情绪统计
GET /api/research/wire | 检索投行研报元数据
GET /api/research/search | 搜索个股券商研报
POST /api/research/vision-analyze | 解读选定研报
POST /api/research/deep-draft | 撰写选定研报的深度解读
POST /api/news/ai-analyze | 解读选定资讯
GET /api/glossary/index | 查阅财经术语
GET /api/search/universal | 统一搜索平台内容
""")

_group("analysis", """
GET /api/portfolio/review | 复盘我的投资组合
GET /api/macro/review | 查看宏观环境复盘
GET /api/briefing/today | 查看今日投研简报
GET /api/ai-supply-chain/capacity-trends | 查看 AI 产业链产能趋势
GET /api/customs-trade/snapshot | 查看海关贸易快照
GET /api/customs-trade/hs-detail/search | 检索海关 HS 商品
GET /api/customs-trade/hs-detail | 查看海关商品明细
POST /api/customs-trade/ai-analysis | 分析海关贸易数据
POST /api/skills/shareholder-changes/scan | 扫描股东变化
POST /api/skills/shareholder-changes/interpret | 解读股东变化
POST /api/skills/cn-earnings/scan | 扫描 A 股财报
POST /api/skills/cn-earnings/diagnose | 诊断 A 股财报
POST /api/skills/cn-earnings/detail | 查看财报诊断明细
POST /api/skills/major-events/scan | 扫描重大事件
POST /api/ai/stock-analysis | 解读个股
POST /api/ai/sentiment | 分析文本情绪
POST /api/fingpt/stock-check | 执行个股证据检查
POST /api/fingpt/news-summary | 总结新闻
POST /api/fingpt/report-analysis | 分析研报文本
POST /api/fingpt/rag-query | 根据指定证据回答问题
POST /api/fingpt/forecast | 分析标的情景
POST /api/fingpt/corridor-risk | 检查风险走廊
POST /api/fingpt/agent-brief | 生成投研简报
""")

_group("tasks", """
GET /api/agents/engines | 查看可用投研引擎
GET /api/agents/tasks | 查看我的投研任务
POST /api/agents/tasks | 创建我的投研任务 | write
GET /api/agents/tasks/{task_id} | 查看我的任务进度和结果
POST /api/agents/tasks/{task_id}/retry | 重试我的投研任务 | write
POST /api/agents/tasks/{task_id}/cancel | 取消我的投研任务 | write
POST /api/agents/deep-research | 启动我的会员深度研判 | write
GET /api/agents/deep-research/{task_id} | 查看我的深度研究结果
GET /api/dulus/memory | 查看我的 AI 历史和记忆
POST /api/dulus/memory | 保存我的 AI 记忆 | write
""")

_group("portfolio", """
POST /api/risk/greeks | 计算期权 Greeks
GET /api/risk/positions | 查看我的持仓台账
POST /api/risk/positions | 新增我的持仓台账 | confirm
GET /api/risk/positions/{position_id} | 查看我的持仓详情
PUT /api/risk/positions/{position_id} | 修改我的持仓台账 | confirm
DELETE /api/risk/positions/{position_id} | 删除我的持仓台账
POST /api/risk/positions/{position_id}/close | 平仓我的持仓台账 | confirm
POST /api/risk/positions/refresh | 刷新我的持仓市价 | write
GET /api/risk/summary | 查看我的组合风险
GET /api/risk/limits | 查看平台风控阈值
GET /api/risk/pnl | 查看我的盈亏汇总
GET /api/risk/pnl/records | 查看我的盈亏明细
POST /api/risk/backtest | 回测风控规则
""")

_group("backtest", """
GET /api/backtest | 查看我的回测
POST /api/backtest | 创建我的回测 | write
GET /api/backtest/{backtest_id} | 查看我的回测结果
DELETE /api/backtest/{backtest_id} | 删除我的回测
GET /api/backtest/aggregate | 汇总我的标的回测
POST /api/backtest/metrics | 计算回测指标
POST /api/quant/lab | 运行量化实验
POST /api/quant/lab/jobs | 创建我的量化实验任务 | write
GET /api/quant/lab/jobs/{job_id} | 查看我的量化实验任务
DELETE /api/quant/lab/jobs/{job_id} | 取消我的量化实验任务
""")

_group("research_tools", """
GET /api/research-tools/catalog | 查看七项投研工具和输入要求
POST /api/research-tools/jobs | 提交收益、组合、财报、机构、基金、债券或因子研究任务 | write
GET /api/research-tools/jobs/{identifier} | 查看我的投研工具任务和报告
DELETE /api/research-tools/jobs/{identifier} | 取消我的投研工具任务
""")

_group("alerts", """
GET /api/me/research-alerts | 查看我的站内提醒
GET /api/me/research-alerts/settings | 查看我的站内提醒设置
POST /api/me/research-alerts/settings | 设置我的站内提醒 | write
POST /api/me/research-alerts/{alert_id}/read | 将我的站内提醒标记已读 | write
GET /api/realtime/recall/subscriptions | 查看我的盯盘订阅
DELETE /api/realtime/recall/subscriptions/{subscription_id} | 删除我的盯盘订阅
POST /api/realtime/recall/subscribe-email | 用我的账号邮箱开启自选盯盘 | confirm
GET /api/weixin/bind/me | 查看我的微信绑定和推送状态
POST /api/weixin/my-push-config | 设置我的微信推送范围 | write
GET /api/account/webhooks | 查看我的 Webhook 订阅
POST /api/account/webhooks | 创建我的 Webhook 订阅 | confirm
DELETE /api/account/webhooks/{sub_id} | 删除我的 Webhook 订阅
POST /api/account/webhooks/{sub_id}/test | 发送我的 Webhook 测试消息 | confirm
""")

_group("data_sources", """
GET /api/data-sources | 查看平台数据源
GET /api/data-sources/{source_id} | 查看平台数据源详情
POST /api/data-sources | 新增共享数据源 | confirm
DELETE /api/data-sources/{source_id} | 删除共享数据源
POST /api/data-sources/{source_id}/sync | 同步共享数据源 | confirm
GET /api/data-sources/module-refs | 查看数据源模块映射
POST /api/data-sources/module-refs | 保存共享数据源模块映射 | confirm
DELETE /api/data-sources/module-refs/{ref_id} | 删除共享数据源模块映射
GET /api/data-sources/items | 检索证据库
GET /api/data-sources/items/tags | 查看证据标签
GET /api/data-sources/corpus-stats | 查看证据质量和覆盖
GET /api/data-sources/items/{item_id} | 查看证据条目
PATCH /api/data-sources/items/{item_id} | 修改共享证据条目 | confirm
DELETE /api/data-sources/items/{item_id} | 删除共享证据条目
POST /api/data-sources/items/{item_id}/interpret | 解读证据条目
POST /api/data-sources/agent-crawl | 抓取网页入共享证据库 | confirm
POST /api/data-sources/keyword-crawl | 按关键词抓取共享证据 | confirm
""", shared=True)

_group("research_library", """
GET /api/pro-research/reports | 检索专业财报库
GET /api/pro-research/reports/{report_id} | 查看专业财报
GET /api/pro-research/reports/{report_id}/chunks | 查看专业财报引用片段
GET /api/pro-research/metrics | 查询专业财报指标
POST /api/pro-research/reports/ingest-item | 将证据条目入专业财报库 | confirm
POST /api/pro-research/reports/ingest-url | 将网页入专业财报库 | confirm
POST /api/pro-research/reports/ingest-workbench-file | 将工作台文件入专业财报库 | confirm
POST /api/pro-research/rag/query | 根据财报库回答问题
POST /api/pro-research/reports/{report_id}/analyze | 分析指定专业财报
POST /api/pro-research/evals/run | 运行共享财报问答评测 | confirm
""", shared=True)

_group("sharing", """
POST /api/report/share | 创建公开的研报解读分享 | confirm
GET /api/report/view/{report_id} | 查看分享的研报解读
POST /api/share/snapshots | 创建公开分享快照 | confirm
GET /api/share/snapshots/{snapshot_id} | 查看分享快照
""")

_group("simulation", """
GET /api/ai-fund/snapshot | 查看 AI 模拟组合
GET /api/ai-fund/arena | 查看 AI 策略竞技场
GET /api/calls/mine | 查看我的投资表态
GET /api/calls/summary | 查看我的投资表态战绩
POST /api/calls | 创建我的投资表态 | confirm
DELETE /api/calls/{call_id} | 撤销我的投资表态
GET /api/ontology/demo | 查看投资本体演示
GET /api/ontology/resolve | 解析标的本体
GET /api/ontology/content-map | 查看内容关联图谱
POST /api/ontology/demo/actions | 记录本体演示动作 | confirm
""")


def _json_body(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return {"required": bool(required), "content": {"application/json": {"schema": schema}}}


# These handlers read Request.json(), so their input is absent from FastAPI's
# schema. Keep the declaration next to the reviewed capability rather than
# teaching the model to improvise undocumented body fields.
_BODY_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/me/referral/redeem"): _json_body({"card_type": {"type": "string", "enum": ["month", "quarter", "year"]}}, ("card_type",)),
    ("POST", "/api/membership/redeem"): _json_body({"code": {"type": "string", "minLength": 1}}, ("code",)),
    ("POST", "/api/support/send"): _json_body({"content": {"type": "string", "minLength": 1, "maxLength": 1000}}, ("content",)),
    ("POST", "/api/news/react"): _json_body({"message_id": {"type": "string"}, "stance": {"type": "string", "enum": ["bull", "bear"]}}, ("message_id", "stance")),
    ("POST", "/api/news/reactions"): _json_body({"ids": {"type": "array", "items": {"type": "string"}}}, ("ids",)),
    ("POST", "/api/me/bookmark"): _json_body({key: {"type": "string"} for key in ("message_id", "title", "topic", "url", "symbol")}, ("message_id",)),
    ("POST", "/api/weixin/my-push-config"): _json_body({"scope": {"type": "string", "enum": ["off", "watchlist", "all"]}, "symbols": {"type": "array", "items": {"type": "string"}}}, ("scope",)),
    ("POST", "/api/calls"): _json_body({
        "symbol": {"type": "string", "description": "A 股六位代码"},
        "direction": {"type": "string", "enum": ["bull", "bear"]},
        "horizon_days": {"type": "integer", "default": 5},
        "conviction": {"type": "integer", "enum": [1, 2, 3], "default": 2},
        "target_price": {"type": "number"}, "note": {"type": "string"}, "source_ref": {"type": "string"},
    }, ("symbol", "direction")),
}

_FRONTEND_INTERACTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("POST", "/api/fingpt/files/extract"): ("上传并提取文件", "需要用户选择文件"),
    ("POST", "/api/data-sources/upload"): ("上传共享证据", "需要用户选择文件及管理员授权"),
    ("POST", "/api/pro-research/reports/upload"): ("上传专业财报", "需要用户选择文件及管理员授权"),
    ("POST", "/api/weixin/bind/start"): ("绑定我的微信", "需要用户扫码授权"),
    ("GET", "/api/weixin/bind/status"): ("完成我的微信绑定", "需要前端扫码会话"),
    ("POST", "/api/backtest/{backtest_id}/run"): ("执行我的回测", "需要消费回测事件流"),
    ("POST", "/api/realtime/recall/subscriptions"): ("配置设备盯盘订阅", "需要前端设备授权与订阅地址归属校验"),
    ("GET", "/api/research-tools/templates/{tool}"): ("下载投研工具输入模板", "请在 QuantLab 投研工具中下载模板或选择文件"),
    ("POST", "/api/portfolio/statement/analyze"): ("复盘交割单文件", "需要用户选择自己的交割单文件"),
}


def capability_id(method: str, path: str) -> str:
    """Stable identity across route order, process starts and operation IDs."""
    return f"{method.lower()}:{path}"


def _resolve_refs(value: Any, document: dict[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    if isinstance(value, list):
        return [_resolve_refs(item, document, seen) for item in value]
    if not isinstance(value, dict):
        return value
    ref = value.get("$ref")
    if ref:
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise ValueError("Only local OpenAPI references are supported")
        if ref in seen:
            # Recursive input objects cannot be expanded forever. A bounded
            # object still leaves HTTP/Pydantic as the final validator.
            return {"type": "object", "description": "Recursive input object"}
        target: Any = document
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        resolved = _resolve_refs(target, document, seen | {ref})
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if siblings:
            resolved = {**resolved, **_resolve_refs(siblings, document, seen)}
        return resolved
    return {key: _resolve_refs(item, document, seen) for key, item in value.items()}


def _inputs(operation: dict[str, Any], path_item: dict[str, Any], document: dict[str, Any], key: tuple[str, str]) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "parameters": _resolve_refs([*path_item.get("parameters", []), *operation.get("parameters", [])], document),
    }
    body = _BODY_OVERRIDES.get(key) or operation.get("requestBody")
    if body:
        inputs["requestBody"] = _resolve_refs(body, document)
    # Only personal AI memory is available through this account assistant.
    # Explicit scope prevents the endpoint's default shared/session scope from
    # being selected accidentally. Its handler still enforces ownership/RBAC.
    if key == ("GET", "/api/dulus/memory"):
        inputs["parameters"] = [p for p in inputs["parameters"] if p.get("name") != "scope"]
        inputs["parameters"].append({"in": "query", "name": "scope", "required": True, "schema": {"type": "string", "enum": ["user"]}})
    if key == ("POST", "/api/dulus/memory"):
        schema = inputs.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
        schema.setdefault("properties", {})["scope"] = {"type": "string", "enum": ["user"]}
        schema["required"] = list(dict.fromkeys([*schema.get("required", []), "scope"]))
    return inputs


def capability_catalog(app: Any) -> list[dict[str, Any]]:
    """Return reviewed JSON operations using the completed application's schema.

    ``parameters`` has the OpenAPI input shape (``parameters`` and optional
    ``requestBody``), with local references expanded. This function does not
    import main, invoke handlers, grant permission, or mutate the app schema.
    """
    document = app.openapi()
    paths = document.get("paths", {})
    catalog: list[dict[str, Any]] = []
    for (method, path), rule in _REVIEWED.items():
        path_item = paths.get(path, {})
        operation = path_item.get(method.lower())
        if not isinstance(operation, dict):
            continue
        try:
            inputs = _inputs(operation, path_item, document, (method, path))
        except (KeyError, TypeError, ValueError):
            # A changed or broken route schema is closed until reviewed.
            continue
        content = inputs.get("requestBody", {}).get("content", {})
        if content and "application/json" not in content:
            continue
        catalog.append({
            "id": capability_id(method, path), "method": method, "path": path,
            "group": rule.group, "title": rule.title, "effect": rule.effect,
            "parameters": inputs, "shared": rule.shared,
            "required_role": rule.required_role,
            "explicit_request_required": rule.effect != "read",
        })
    return catalog


def get_capability(app: Any, capability: str) -> dict[str, Any] | None:
    return next((item for item in capability_catalog(app) if item["id"] == capability), None)


def boundary_summary(app: Any) -> dict[str, Any]:
    """Describe honest boundaries without advertising unsafe callable tools."""
    paths = app.openapi().get("paths", {})
    interactions = [{
        "id": capability_id(method, path), "method": method, "path": path,
        "title": title, "reason": reason, "frontend_interaction": True,
        "enabled": False,
    } for (method, path), (title, reason) in _FRONTEND_INTERACTIONS.items()
        if method.lower() in paths.get(path, {})]
    return {
        "frontend_interactions": interactions,
        "excluded": [
            "管理员内部运营、鉴权凭据和模型密钥配置",
            "任意外部 MCP 工具调用、网页代理和递归 AI 聊天入口",
            "二进制下载、第三方原文授权令牌和持续事件流",
        ],
        "shared_write_policy": "共享数据源和专业财报库的写操作需要管理员身份与本次确认。",
    }
