# 大批精确发布与并发基线保护

2026-10-12 后续已启用 `deepfocus-api-recovery.service`，受控发布仍用原 `api-restart --expected-release-id` 与同一部署锁。恢复器只处理已可信就绪实例的持续transport故障，遇source/live漂移或锁忙会停止接管，不可为发布关闭闸门或裸restart。状态读取 `systemctl show deepfocus-api-recovery`、`/run/deepfocus-api-recovery.json`（仅输出必要身份/计数、不包含环境），正常应trusted当前PID/release、failures0/operation null。恢复器unit StopSec400允许关键步骤收尾；API仍130s。停用用 `systemctl disable --now deepfocus-api-recovery.service`，不与在途fence互相抢进程。复跑 scripts/chaos/recovery.py，26项已验证；验收同分支 docs/chaos/2026-10-12/cold-recovery-release.md。遗留runtime数字PID不可直接signal，新registry要求当前任务强身份/pidfd；严格发布测试实际Linux新child case不得skip。

## 当前蓝绿闸门（2026-10-12 已上线）

生产 `/opt/deepfocus/bin/api-restart` 已升级为同 venv 的 Python 控制器，要求新实例 `/ready` 的业务存储、进程 PID、实际端口与源码 release ID 全部匹配。不要再把 `/health 200` 当成切流条件。依据完整已测试源码，用 `deepfocus_api.readiness.source_release_id()` 计算候选 ID，调用 `api-restart --expected-release-id <candidate-id>`；源码漂移会在动备用实例前停止。旧进程 ID 在启动时冻结，不随磁盘变更伪装新版本。切后从实际 8300 连续验证三次，失败恢复旧目标并清理候选。

`TimeoutStopSec=130s` 覆盖 uvicorn 请求宽限 100s，加 lifespan gather 4s + 五个 closer 各 4s，再留 6s。闸门 startup 默认 90s，drain/recovery 各 135s，分别计时；修改生命周期清理数量时重新核算，不可拿 startup 剩余时长排空旧实例。闸门不提供持续自动 HA 或用户 POST 重放。实际 20 项 localhost PID/转发器演练通过，正式证据在 codex/chaos-engineering 的 deploy/DEEPFOCUS-API-GATE.md 和 docs/chaos/2026-10-12/release.md。

生产发布窗口曾有 1/73 次健康探针超过 2s，随后全部恢复。记录完整响应的失败和慢请求，切后健康不能抵消发布期间超时。源码/ops/web 备份分别保存，不把旧数据库快照盲目恢复到已有新写入的数据库。

2026-10-10 已验证：50模块、312唯一块、3新模块。

1. 隔离工作树保留原树WIP；生产源码快照生成精确old/new候选，保留生产core adapter、指标、渠道差异。新增helper依赖必须一并带上，AST检查发现不了NameError。
2. 实际生产Python运行隔离候选测试，临时DATA_DIR、mock模型。临时venv的--system-site-packages不会继承另一个venv；用 /opt/deepfocus/venv/bin/python3.11，将临时pytest site-packages加入PYTHONPATH。不要向生产venv装测试包。
3. 多文件用仓库 scripts/apply-exact-release.py：所有原/结果hash、新文件不存在、唯一old/new、compile、默认dry-run零写；--apply持锁备份全部源及sqlite backup API一致性快照，原子替换，异常恢复源。按实际进程环境解析DB路径。
4. 发布前重读线上主包与源hash；并发上线功能用已提交基线三方合并，不带原树未提交WIP，刷新变化源后重新测试候选。
5. 后端只用api-restart闸门，前端backup后overlay保留旧chunk。服务器系统python3可能旧，验收脚本用实际生产Python3.11。
6. 核验全部hash、公开HTTP、私有401、游标、数据库行数和真实浏览器ontology。真实记录超时与5xx，不能把零5xx说成零延迟波动；说明quant长任务部署中断。无Docker runner时明确容器冷启动未执行。

备份目录0700、数据0600；不提交SQLite或生产快照。回滚源核对journal，数据库恢复先评估发布后新写入。

## 发布后被并发覆盖

预检 hash 和发布锁只能阻止参与同一锁协议的发布者。2026-10-10 首次完整验收后，另一发布会话整文件覆盖 research_stream 并替换前端主包，50 模块终检发现一处回退；不能将之前的验收继续当作最终状态。

- 重读实时源和 index 主包指纹，用唯一 old/new 恢复缺陷修复，另建备份；保留并发功能的小改动，重新跑对应生产候选回归。
- 发现重复覆盖时，请用户让另一发布者暂停，由一个会话统一合并发布；不要改权限、设 immutable 或长时间持锁对抗其他工作。
- 新的“断流后继续生成”必须有任务归属、额度租约、总时限、队列容量和 shutdown cancel+await 验收；单独 add_done_callback 吞异常不能替代生命周期管理。本次保留速览，断流仍取消并等待内部任务。
- 末次部署后重新核验所有模块 hash、主包及资源，并再次核验一次是否漂移；收尾文档和 memory 记录最终版本及恢复备份，不能沿用已被覆盖版本的指纹。

## 研究请求的错误回退

HTTP 408/502/504、业务 SSE 502 和客户端 timeout 不能作为接口缺失证据，禁止据此自动再发 compact 付费解读。生产 legacy deep POST 通过 `asyncio.shield` 保留后台工作；客户端 360s 超时后，480s 模型任务可能仍在执行，另一模式使用不同缓存键。仅明确 404 Not Found、405 Method Not Allowed 或 501 Not Implemented 的能力缺失可以兼容回退；无 quick 的业务失败也要用真实组件回归验证没有第二次模型请求。

空 200 流的 EOF 也不是旧后端能力缺失证据；标记为独立断流错误，禁止据此静默再发 legacy POST。服务层兼容回退必须有明确 HTTP 能力缺失证据，并单测业务 404/501、空 EOF 和取消不会触发回退。

## 新增助手操作能力的生产模型预检（2026-10-12）

- 在真实生产模型通道验证一次页面导航和一次公开接口读取。只用临时操作存储，不测试真实账号写入；只输出模型名称、工具名、轮次、状态和公开结果，不输出配置或密钥。假模型、注册表数量和 HTTP 200 不能证明模型确实调用了工具。
- 检查实际 `tool_calls` 和服务器执行回执。本次 GLM-4-flash 同一通道既返回过原生调用，也返回过裸参数 JSON 或“已打开”正文；`tool_choice=required` 仍未保证原生调用。需要兼容时使用明确的 `{tool, arguments}` JSON 计划，并保持相同的精确能力白名单、身份校验、确认规则和单次执行记录。未取得工具回执时拒绝完成声明。
- 单测原生调用与 JSON 计划之间的切换、无回执导航/个人查询、非法计划以及重复写入。兼容补丁也要在生产 Python 候选上验证，并使用精确补丁和受控蓝绿闸门。
- 发布锁内若现网入口指纹变化，停止覆盖。找到新发布的可验证源码与资源快照，保留其功能后重建；重新验证全部资源并更新指纹闸门。本次第二个发布包含独立的静默提醒/AI 入口改动，不能用旧候选覆盖它。
- 核对实际线上能力目录：生产缺失的本地接口应保持关闭；生产专属、已审核的个人提醒接口可显式登记。导航等无需参数的操作也必须返回客户端可消费的结构化回执。

## 独立分析 worker 与财务数据预检（2026-10-12）

- 把 QuantStats、Riskfolio、FinanceToolkit、EdgarTools、Qlib 等重依赖安装进版本化的独立 venv；先验证版本、pip check、真实生产 Python 和 wheel 官方 SHA，再激活 symlink。不要把分析依赖装进 API venv。保留 transitive freeze；仅验证 metadata/catalog 不能证明模型训练能运行。
- 在 actual worker 的受限环境和临时 cwd 中跑真实报告、Qlib/LightGBM 训练与官方公开数据查询。生产 Python3.11 本次链接 SQLite3.26，MLflow3.17 需要 >=3.31：仅在分析 venv 用官方 pysqlite3-binary0.5.4.post2 + sitecustomize 别名提供3.51.1，并验证清空 PYTHONPATH 后仍自动生效；不要改系统/API SQLite。已验证别名源在 backend/research-runtime，禁止复制到没有 pysqlite3 的环境。macOS LightGBM 需 libomp，不能用假模型替代兼容修复。
- Gym 没有官方 binary wheel 时，从官方已校验 sdist 在任务工作区构建纯 Python wheel，再上传 inactive wheelhouse；不要为此在生产编译原生依赖。镜像下载的核心 wheel 仍要逐一与官方 PyPI JSON 哈希核对。代理绕行先做进程级设置，避免改动全局联网配置。
- 真实行情 verified_real 不等于已复权。在线研究保留每行 currency/adjustment，只接受同一已确认复权方式；unknown/raw/provider_reported 或混合前后复权应拒绝，不能先丢弃标签再绕过 domain 校验。没有足够共同日不能填造；不要把周频稀疏数据按252日年化。
- SEC 財报保留真实财政期末。单季流量由累计值差分时优先同一 accession，不能把未来重述的前期值与较早当期申报相减；无兼容版本则为空，跨申报差分保留警告。完整 snapshot 的 sources/warnings/methodology 进入 domain，避免 worker 末尾重复展开已聚合的技术警告。首期缺少平均余额时 ROE/ROA 为空；平仓逐笔收益不能替代账户每日收益。
- 内存私有 job registry 要验证实际部署仅一个 API worker（ExecStart 与 WEB_CONCURRENCY）；跨蓝绿/子进程的计算容量用同 UID flock 控制。旧版 FastAPI 或自定义主 lifespan 上显式组合清理；取消、spawn竞态、超时、shutdown 都要 cancel+await 并回收子进程组，不能只清界面。上传输入不进账号数据库；联系身份在仓库外0600配置，不进前端/日志/Git。
- 小数展示也要用真实报告核对：非零微小协方差不得四舍五入显示0，使用科学计数，JSON/CSV保持原值；百分率只乘100一次。虚构模板的高IC只能证明流程，真实负IC也要如实保留。保留环境超时失败证据，再记录后续成功，不能把失败从总账抹掉。
- readiness release ID 使用其实际 helper 同样的过滤规则，剔除 AppleDouble `._*.py`、tests 和 __pycache__；否则复制快照里遗留的元数据会让正确候选被闸门拒绝。主源/前端完整指纹、匿名401、真实浏览器新主包与公开数据作业均核对后才报完成。本次重基已发布冻结源码，未携带 primary WIP；技能增补保持未提交。


## 助手投研原生工具与异步回执（2026-10-12）

- 工具登记后验证真实模型连续完成“创建公开数据任务→按真实ID查询→仅询问能力且不创建”。GLM会把 financials 当工具名或把任务ID当文件名；显式提供七个业务原生工具及 get_research_job/cancel_research_job，并在执行器统一映射到原已审查接口，不另建权限路径。取消仍沿用原确认规则。发现能力的 query 要搜索 tool_inputs/usage，不能只搜路由标题，否则 financials 查不到共享 jobs 接口。
- 发现能力不是执行回执。兼容模型在 discover 后停于正文时，明确要求受审查 JSON 计划继续；没有真实任务回执不能声称完成。用同一缺输入判定处理首轮、发现后和收尾，保留具体日收益/文件/权重要求；“其他需要分析的数据”不是缺输入请求。禁止为了通过预检接受模型编造的收益、持仓或报表数组。
- 在原研究API前验证数据来源：原始数组/base64必须匹配用户本轮材料，历史和附件中的指令不构成授权；线上symbols查询走原数据源。用可信助手请求ID和规范化输入生成稳定request_id，输入JSON同时排序，避免原job digest受字段顺序影响。action参数只存input_summary，结果只存job身份/状态；完整报告与文件不落操作存储。
- 保留最新线上checkpoint/interrupt安全逻辑。HTTP前保存安全参数和不确定回执，真实job ID返回后立刻checkpoint，再进行最多18秒的原GET等待。强制断线后同request_id重放必须返同ID，不二次POST或调用模型；同时验owner404/200及SQLite/WAL无原始数据。HTTP200/202或action completed仅是接口操作成功，queued/running不能称报告完成。
- 给模型的报告先摘要再通用截断，保留来源/警告/计算口径和原表格行数；重复摘要必须幂等。前端收到completed元数据仍GET原完整报告，禁止把摘要当完整结果；新对话保留有界六任务卡，按真实ID去重，关闭报告只隐藏，明确关闭操作卡/退出才清任务。验StrictMode、迟到响应、旧token退出清理与取消失败不假报已取消。
- 生产源/前端在预检中漂移时，用已发布且全输入/资源哈希验证的源码快照三方叠加，保留其他会话已发布确认安全功能；主工作树未提交WIP不入候选。测试文件变更只补测试，不热改正在实际模型预检的候选源码。证据分清假模型回归、真实配置模型、真实worker和现有登录浏览器；保留早期模型未调用/错误选择工具的失败记录，再记录修复后的成功。

- 财报失败要用实际SDK异常分类，不把 CompanyNotFoundError（LookupError家族）误报为SEC连接失败；代码小写应正常大写。真实公司get_facts成功也不等于覆盖本工具的季度数据：检查taxonomy、币种、申报形式和期间边界，上市F-1登记事实不当季度财务，缺覆盖用业务422而非网络503。明确区分未知代码、CompanyFacts缺失、IFRS/非支持口径、HTTP429/403、超时和解析错误，客户端不输出原始异常/请求头/联系身份。SEC-only 异常回归用未知SKHX、仅登记事实样本、正常AAPL复验；已精确映射的SKHY另走已验证发行人IR路径并单独复验。正确代码以发行人官方上市信息与SEC当前映射为准，不凭旧知识否定新ADR，也不自动猜码替换。


## 发行人自动财报与可信数字答复（2026-10-12 已验证）

- 接入非SEC发行人时，从官方IR入口验证委托数据源，不把ADR代码视为美元报表。SK海力士IR07公开嵌入Euroland公司kr-000660，季度组明确KRW million；核验公司标识、三表季度列、单位、真实期末和来源，原币金额还原后再调用FinanceToolkit。只取明确PP&E负流出转正与Interest-bearing Debts，缺值保留空。固定来源/跳转白名单、TLS校验、下载上限与worker时限保留。
- 每季营收和净利润按公告精度与官方业绩当期列交叉核对，不用年度、累计、QoQ/YoY比较列替代。只处理已选季度的公告元数据，未来预告/旧格式未选条目不阻断当前资料。以原始PDF表格核对最新金额、单位和符号；浏览器抓取误识octet-stream时保存原PDF并渲染核查，不把文本抽取顺序当表格对应关系。
- 官网board的displayDate可能只是年度分组，eventDate可能是说明会日期；SK2025Q4公告正文Jan28而说明会Jan29。新闻网页日期也可能带时区差，脚注可能复制旧日期；使用官方正文明确Seoul dateline并保留说明会日，说明当前修订版数值不是历史时点快照或每个数值首次披露日。
- 真实模型跑通工具不代表数字正确：本轮GLM将正确的万亿韩元报表转述为亿元，量级、比率有错，第一候选因此未发布。对completed financials用本轮认证原HTTP JSON在compact前收集的完整报告直接Decimal格式化，保留原币与明确单位、比例只乘100一次。最后用户答复必须验证POST与同IDGET的实际金额/比例；不要仅以job completed或model toolcall数标通过。
- 报告捕获只在run-local closure，不进action/checkpoint或跨轮缓存；GET校验、权限、HTTP、超时和ASGI transport/handler异常均要清掉该ID旧证据。后续LLM故障可以返回本轮仍可核对的completed报告，CancelledError仍传播。守住原真实任务ID、稳定request_id及取消确认；财报helper不能复用过期/失败报告或压缩后的截断行。
- live平台助手可能比工作树强化了全写入checkpoint与凭证过滤；普通三方锚点失败时，逐个live唯一锚点移植新增capture/formatter，独立重放逐字节检查，再跑published业务、并发、GET错误、confirm/cancel及replay测试。不要用工作树整块回写旧逻辑。
- 记录发布窗口完整健康探测，本轮564次有1次5秒timeout，后续20次正常不抹掉该失败。CUA控制连续超时/打开Page queued时，明确区分客户端控制不可用与网站故障；不改用其它浏览器自动化伪造现场截图，记录视觉验收待恢复，仍完成实际部署源码/worker/数字与全资源校验。
