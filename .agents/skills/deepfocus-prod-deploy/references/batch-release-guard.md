# 大批精确发布与并发基线保护

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
