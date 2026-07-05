# daocaijing.com SEO/GEO 站长操作清单

代码侧曝光面已全部就绪（2026-06-27 上线）：全 A 5863 个股页 + 复盘 + 60 问答页 + 40 术语科普 + sitemap/robots/llms.txt/feed.xml + 动态 OG 卡 + 全站 JSON-LD。
**下面这些是代码够不到、只能你（站长）操作的，做完才算"通电"。** 按优先级。env 都设在生产 systemd 服务（`/etc/systemd/system/deepfocus-api.service` 的 `Environment=` 或 EnvironmentFile），改后 `systemctl restart deepfocus-api.service`。

---

## P0 ① 百度站长平台（国内最重要，ziyuan.baidu.com）
1. 注册并添加站点 `https://daocaijing.com` → 选「HTML 标签」或「文件」验证。
   - 文件验证：拿到文件名/内容后，把内容设进 env，会从 `/百度给的文件名` 直出（或我帮你加精确路由）。
   - 标签验证：把 code 设进 `DEEPFOCUS_BAIDU_SITE_VERIFICATION=codeva-xxxx` → 所有 SEO 落地页 `<head>` 会带 `baidu-site-verification` meta；**首页验证需把同 meta 加进 `public/index.html` 再前端重建**（首页是静态文件）。
2. 验证通过后：**链接提交 → 普通收录 → 提交 sitemap**：`https://daocaijing.com/sitemap.xml`。
3. **拿"主动推送"的 token** → `DEEPFOCUS_BAIDU_PUSH_TOKEN=xxxx`（重启后，后台任务每 30min 自动把新页推给百度，这是冷启动收录最快的路径，否则该功能是 no-op）。
4. 「抓取诊断」实测 `/review`、`/stock/600519`、`/qa`、`/learn/pe-ratio` 返回 200。

## P0 ② Bing Webmaster（bing.com/webmasters）+ IndexNow
1. 验证站点（可从 Google Search Console 一键导入，省事）。提交 sitemap。
2. **IndexNow**：生成一个 key（任意 32 位十六进制字符串）→ `DEEPFOCUS_INDEXNOW_KEY=<key>`。
   - 重启后 `https://daocaijing.com/indexnow-key.txt` 会返回该 key（已写好路由）。
   - 后台任务会自动把新页推给 IndexNow（Bing/Yandex 共用）。
3. （可选）Bing 站点验证：`DEEPFOCUS_BING_SITE_VERIFICATION=<code>` → `/BingSiteAuth.xml` 自动生效。

## P0 ③ Google Search Console（search.google.com/search-console）
1. 验证站点（DNS 或 HTML 标签 `DEEPFOCUS_GOOGLE_SITE_VERIFICATION=<code>`）。提交 sitemap。
2. 「网址检查」实测几条 URL 可抓取、已编入索引。

## P1 ④ Web Push 召回（装依赖 + 配 VAPID）
免费用户关页也能被叫回（两日留存头号修复），现在缺依赖会静默降级：
```
/opt/deepfocus/venv/bin/pip install pywebpush
# 生成 VAPID 密钥对（pywebpush 自带工具或 web-push 生成），设三个 env：
DEEPFOCUS_VAPID_PUBLIC_KEY=...
DEEPFOCUS_VAPID_PRIVATE_KEY=...
DEEPFOCUS_VAPID_SUBJECT=mailto:你的邮箱
```
重启后 `enableBrowser`/`?watch=` 落地的盯盘召回才真正生效。

## P1 ⑤ 微信订阅号每日复盘连载（零执照、今天能起）
1. 注册个人/企业**订阅号**（发内容不需营业执照）。
2. 拿到 `appid`/`secret`，把**服务器出口 IP 加进公众号「IP 白名单」**。
3. 跑导出脚本（脚本与字体已部署在 prod `/opt/deepfocus/tools/syndicate/`）：
```
cd /opt/deepfocus
WECHAT_APPID=wx.. WECHAT_SECRET=.. DEEPFOCUS_API_BASE=http://127.0.0.1:8300 \
  DEEPFOCUS_CARD_FONT=/opt/deepfocus/tools/syndicate/fonts/wqy-microhei.ttc \
  /opt/deepfocus/venv/bin/python3.11 -m tools.syndicate.wx_mp_export --publish
```
推到**草稿箱**，去公众号后台核对后**人工点群发**（每天 1 次额度）。可配 cron 每交易日 15:50 自动生成。

## P1 ⑥ 头条号/百家号一稿多发（亿级内容池 + 高质量外链）
1. 注册头条号/百家号/企鹅号。
2. 跑包导出：
```
cd /opt/deepfocus
DEEPFOCUS_API_BASE=http://127.0.0.1:8300 \
  DEEPFOCUS_CARD_FONT=/opt/deepfocus/tools/syndicate/fonts/wqy-microhei.ttc \
  /opt/deepfocus/venv/bin/python3.11 -m tools.syndicate.headline_pack
# 产物在 ./syndicate_out/pack_<date>/：titles.txt / copy.txt / card_1..4.png / backlink.txt
```
平台后台新建图文 → 粘 `copy.txt` + 传 `card_*.png` → 发布。文末裸链回 `daocaijing.com`。

## P2 ⑦ ICP 备案 + 实体信号
- 确认 daocaijing.com 是否已 **ICP 备案**（中国机房合规要求，也是百度信任信号）。
  - 有：`DEEPFOCUS_ICP_BEIAN=京ICPxxxxxxxx号` → 所有 SEO 页 footer 自动显示。
  - 无：尽快办（合规/被阻断风险，不只是 SEO）。
- 官方可验证社媒账号（公众号/微博/小红书主页 URL）→ `DEEPFOCUS_ORG_SAMEAS=https://...,https://...`（逗号分隔）。**sameAs 是 AI 引擎做品牌归因的最高杠杆信号**。品牌红线：禁出现「道财经」，只用 daocaijing.com。

---

## 依赖关系（排期）
**P0 ①②③ 的"验证+提交+token"是收录从 0 起来的总闸门**，无依赖、并行做，1–2 小时后台操作即可。做完后，代码侧的主动推送/IndexNow/sitemap 才真正"通电"，否则一直是 no-op 空转。④⑤⑥ 并行起量。⑦ 有 ICP 风险则最优先升级法务。

每项设完 env 记得 `systemctl restart deepfocus-api.service`。
