# 新闻与文章 AI 解读验收

2026-10-12 实测流程。适用于「文章解读草率」「信息边界」「0% 却解读完成」「原文有措施却说不明」「重新解读仍读到旧卡」等问题。

## 先对照输入与输出

1. 用只读 SQLite 连接定位具体文章，检查 title、content、url 和缓存实际 payload。记录正文长度、具体事实、confidence、logic_lines 和 source_note；不要仅从截图猜测模型能力。
2. 区分已存正文、原文提取结果和最终送模型的 body。图片/PDF 链接经轻量 HTML 抓取直接取 `response.text`，会把二进制误当正文；原本只有 80 字的有效正文可能被 7000 字乱码覆盖。
3. `_fetch_article_text` 必须先拒绝媒体后缀，再检查响应 MIME、文件魔数和控制字节。取得已清洗 `ArticleOriginalText` 后，新闻模型调用传 `url=None`，保留可信正文，不再二次抓图。
4. 来源标签只表达实际覆盖：stored-text 为「已读取已收录正文」；stored-fallback 和 truncated 说明有限覆盖。不要只因拿到非空文本就写「全文」。短 stored-text 也可能有完整事实，不能仅因不足 80 字丢弃。

## 对齐生成与缓存门槛

- 新闻响应使用 `normalize_news_result`，不调用研报 `ensure_logic_lines`。后者会制造「信息边界」四句占位。
- 同时检查内层 `NEWS:v5`、外层 `news:v5` 和本机 `news:v5:` 缓存。零置信、标题复述、空内容、纯「影响未知/信息边界」卡不能被当成成功，也不能进入长期缓存。
- 不强凑逻辑线：摘要已经补充具体措施、数字或主体时，空 logic_lines 可以正常阅读。后端与前端质量判断必须一致；「标题和导语」中真实存在的短事实不等同于仅标题。
- 模型质量不足时补救一次；仍失败则可重试，不冒称原文不存在。新闻专用 retry_schema_hint 不能再被通用 JSON 修复里的每项 18/24 字限制压缩。
- 两次质量尝试各自包括模型内部 JSON 修复，实际预算 55/70 秒，总计不超过 125 秒，给 CoreAgent 的 150 秒限额留出余量。超时取消必须传播，不缓存失效结果。
- 用户明确点击重新解读时，refresh 绕过两层服务器缓存和本机内存/持久缓存；仍走原会员/额度门。
- 历史重新解读保留正文，并剥离消息 id 的 `news:v5:` 前缀。账号切换后，旧响应不能写新账号缓存。

## 运行回归与真实样本

从 backend 目录用项目 Python 跑：

```sh
.venv/bin/python -m pytest deepfocus_api/tests/test_news_quality_api.py deepfocus_api/tests/test_research_vision.py deepfocus_api/tests/test_article_original_text.py deepfocus_api/tests/test_research_latency.py deepfocus_api/tests/test_research_multi_agent.py deepfocus_api/tests/test_llm_json_retry.py -q
```

前端跑 aiInterpretCache、newsInterpretationQuality 与既有研报重试组件测试，再做 tsc 和构建。覆盖 HTTP 200 烂卡变可重试失败、两项措施保留、手动刷新、老缓存绕过、账号迟到响应、历史重生成和短正文。

再用实际生产 Python、生产源快照加精确补丁组成隔离候选。临时 DATA_DIR、mock 模型，不给生产 venv 装 pytest。真实模型样本使用公开文章正文，并在隔离数据目录运行；验证具体措施完整、无乱码与占位，而不只看响应是否成功。

## 发布及收尾

保留线上研报 `_STYLE_RULE` 与新闻 `_NEWS_STYLE_RULE` 的分叉，不把本地新闻同名规则覆盖到线上研报。现有模块使用唯一 old/new 及完整 hash manifest 发布；新 helper 的依赖要带齐。

从已发布前端源码冻结基线叠加本次变更，排除其他会话 WIP。webpack context 会改变模块/分块编号；若 hash 不同，比较模块内容和导入关系，不能据「看起来差不多」发布。CSS 无变更时应字节一致；sourceMappingURL 注释也会改变 CSS hash，生产构建显式关闭 sourcemap。

按主技能备份、蓝绿重启、静态资源 overlay 并最后原子换 index。核验全部生产源和资源 hash，以及真实浏览器公开路由。匿名文章解读 403 是会员保护生效，不能把它写成成功的会员端到端测试，也不要为测试临时扩大账号权限。保存真实模型输出、已认证测试或组件回归证据，并在收尾再次检查部署指纹是否漂移。
