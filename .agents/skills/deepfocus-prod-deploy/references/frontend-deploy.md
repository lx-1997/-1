# 前端全量重建发布 — 详细步骤、验证、回滚

## 前置认知
- 线上前端 = `FinancialTerminal`(TERMINAL_ONLY 终端),nginx 静态根 `/var/www/deepfocus`。
- **重建 = 发布当前 HEAD 的全部前端状态**。HEAD 常比线上新(有"待发版"的已提交改动)。重建会把这些一起推上线 → **发布前必须告知用户、确认**(面向用户、不可逆)。
- 构建配置已固定在仓库:`.env.local`(`REACT_APP_TERMINAL_ONLY=true`、`REACT_APP_AUTH_BYPASS=false`)+ `package.json homepage:"./"` + apiClient 同源。**别再设 `REACT_APP_API_BASE_URL`**(旧 runbook 的写法,会烘焙死 IP → 域名访问混合内容被拦)。

## 步骤
1. 构建:`CI=false npm run build` → `build/`。
2. 确认改动进 bundle(中文转义,用 Python 找 `\uXXXX`,见 SKILL 坑③)。
3. 干净 tar:`COPYFILE_DISABLE=1 tar --exclude='._*' --exclude='.DS_Store' -czf /tmp/df-fe.tgz -C build .`;`tar tzf ... | grep -c '/\._'` 应为 0。
4. 发布前核对服务器独有文件(应为空):
   ```bash
   ls build/ | LC_ALL=C sort > /tmp/lb.txt
   ssh root@39.105.214.141 "ls /var/www/deepfocus | grep -v '^\._'" | LC_ALL=C sort > /tmp/sw.txt
   comm -23 /tmp/sw.txt /tmp/lb.txt   # 空 = overlay 安全
   ```
5. 备份 + overlay 解压 + 清 `._*` + chown(见 SKILL 流程 B 第 4 步)。**用 overlay 不要 `rm -rf www/*`**:overlay 保留任何服务器独有文件;旧的 content-hash chunk 残留无害(老 index.html 缓存的用户还能加载旧 chunk)。
6. 验证:服务器本地 + 浏览器 UA 打 `https://127.0.0.1 -H "Host: daocaijing.com"`,首页/主 bundle/`/ai-fund` 全 200;on-disk 确认 index.html 指向新 `main.<hash>.js` 且 bundle 含改动。

## 回滚
```bash
ssh root@39.105.214.141 '
  cd /var/www/deepfocus && tar -xzf /root/deepfocus-web.bak-<时间戳>.tgz   # overlay 回旧产物
  # 或更彻底:先清 static 再解 bak。index.html no-cache,回滚即时生效。'
```
备份命名 `/root/deepfocus-web.bak-<YYYYmmdd-HHMMSS>.tgz`(同 /root 下还有历史 `deepfocus-web.bak-*` / `deepfocus-fe-bak-*`)。

## 坑(详见 SKILL.md)
- `/ai-fund` 独立目录 `/var/www/daocaijing/ai-fund.html`,发前端不碰。
- 裸 curl 被 nginx bad-bot(`curl/` UA)拦 444 → exit 56,验证要带浏览器 UA。
- Terser ascii_only:bundle 中文转义,grep 中文搜不到。
- index.html no-cache(`location = /index.html`),部署即时生效,用户 F5 即吃新 bundle。

## 隔离构建与手机报价验收（2026-10-12 已验证）

- 未提交源码不一定是未发布功能。若本地已有产物与线上主包 SHA256 完全一致，逐处核对当前未提交差异是否已进入该线上包；确认为现有生产状态后，把对应源码冻结到隔离工作树，再叠加本次补丁。不要从落后的干净 HEAD 发布而回退线上功能，也不要把无法证明已发布的 WIP 顺带上线。
- 隔离工作树优先链接原项目 `node_modules`。本项目依赖中存在 `node_modules/node_modules` 的绝对链接；克隆整棵依赖曾导致双份 React，主包 `Minified React error` 标记从 1 变为 2，恢复共享依赖链接后回到 1。不要部署该重复运行时产物。构建目录改变也可能改变 Webpack 模块 ID，主包文件名变化本身不能证明业务代码不同。
- 发布前记录现网 index 和主包 SHA256；在同一发布锁内重核对，变化则停止当前候选发布。验证全部入包资源哈希后先安装资源，最后原子替换 index.html；保留旧 chunks 和服务器独有文件。发布后再次核对真正返回的 index、主包、CSS 和资源，不能只看磁盘目录。
- 金融数字必须额外检查单元格截断。页面没有横向溢出，不代表报价完整显示；千位分隔符可能让四位数价格出现省略号。用 390px 和 320px 视口检查含千位分隔符的真实报价，确认价格列 `scrollWidth <= clientWidth`。本次用固定数值列宽、代码与名称分行保留完整报价；CSS 修正后 JS SHA256 不变，证明没有顺带变更业务逻辑。


## 研报阅读器、缩放容器与打印验收（2026-10-12 已验证）

- 排版验收需加载真实宿主全部 CSS，并按生产加载顺序挂载组件。`.terminal-only-root .bbt` 的 `--z` 随视口变化，深度研报弹窗宽高应先将视口除以 `var(--z, 1)` 再扣逻辑边距；只看组件样式会漏掉放大窗口越界。
- 报告发布日期与 AI 生成时间是不同字段。小屏可以隐藏正文已保留的重复标题，但不得因正文存在生成时间就隐藏报告发布日期。移动操作栏增加日期行时，用 `flex: 0 0 auto` 防止被正文挤到最小高度；验收应同时满足日期底部不超过操作栏底部、正文不与操作栏重叠。
- 默认收起的 `details` 必须额外验收打印内容。打印前临时打开摘要、打印后恢复原状态，并在卸载时清理监听器；现代浏览器可以配合 `::details-content` 的可见性覆盖。实际生成 PDF，逐字检查全部摘要段落和报告发布日期，不能只检查页面无裁切或 DOM 中存在文本。
- 复用依赖目录时，隔离每次构建的 Webpack、Babel、ESLint 缓存，避免并发候选共享缓存；保持唯一 React 运行时，与基线标记数比较。候选源码与当前现网冻结输入逐文件对齐，构建期间现网变化则重新寻找可证明的最新基线。
- Nginx 可能通过既有 sub_filter 追加教程和模拟盘入口，导致 HTTP HTML 与磁盘 index 的哈希不同。分别验磁盘完整哈希、HTTP 返回的构建前缀/新资源地址，以及 JS/CSS HTTP 完整哈希；不要为了让整页哈希相等而修改已有 Nginx 行为。
