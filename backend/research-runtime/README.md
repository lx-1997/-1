# 独立分析环境

API 服务与分析进程使用不同虚拟环境。锁定六个分析引擎的版本见 `../requirements-research.txt`。发布时核对 wheel 来源、`pip check`、实际 Python 版本和完整依赖 freeze，并跑真实库测试与真实 worker。

生产 Linux 的 API Python 链接 SQLite 3.26。MLflow 3.17 的临时跟踪库需要 SQLite >=3.31，所以独立分析环境安装 `pysqlite3-binary==0.5.4.post2`，并将本目录已验证的 `sitecustomize.py` 复制到该环境的 `lib/python3.11/site-packages/`。它为这个环境提供 SQLite 3.51.1。不要将它复制到 API 环境或系统 Python；macOS 本地验证使用原生 SQLite，无需该别名。没有安装 pysqlite3 的环境不能加载此文件。

复制后清空 PYTHONPATH，再用分析环境的 Python 核对 `sqlite3.sqlite_version` 和生成列语法，并执行一次真实 Qlib/LightGBM 训练。分析模块仍将 MLflow 跟踪放在每任务临时 SQLite 文件中，任务结束删除，未连接业务数据库。macOS LightGBM 还需要系统 `libomp`。

SEC 联系身份必须留在代码仓库外的 0600 配置文件中，不写入 requirements、文档、前端或 Git。
