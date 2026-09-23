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
