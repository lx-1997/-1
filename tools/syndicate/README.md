# 复盘内容多渠道分发（syndicate）

把每日 A 股复盘一鱼多吃到自带分发的内容平台，做公域曝光 + 外链回流。运营手动触发，与部署的 FastAPI 服务解耦。

## 取数
默认打 prod 本机 `http://127.0.0.1:8300`（绕 nginx 前端标识守卫）。在别处运行用 env 配：
- `DEEPFOCUS_API_BASE`（如 `https://daocaijing.com`，此时还需 `DEEPFOCUS_FRONT_TOKEN` 过 df_web_ok 守卫）
- `DEEPFOCUS_CARD_FONT`：图文卡的 CJK 字体路径（缺则按内置候选找；prod 已放 `tools/syndicate/fonts/wqy-microhei.ttc`）

合规：标题/正文均过 `neutralize_text`（禁「建议买入/目标价/暴涨」等），品牌只用 DeepFocus / daocaijing.com（禁「道财经」）。

## 微信订阅号 → 草稿箱
```bash
# 预览（不外发，本地写 body.html + 封面 + 标题候选）
DEEPFOCUS_API_BASE=http://127.0.0.1:8300 python3 -m tools.syndicate.wx_mp_export
# 真推草稿箱（需公众号 appid/secret，且把服务器出口 IP 加进公众号 IP 白名单）
WECHAT_APPID=wx.. WECHAT_SECRET=.. python3 -m tools.syndicate.wx_mp_export --publish [--date 2026-06-26]
```
推到**草稿箱**不直接群发——去公众号后台「草稿箱」核对后人工点发布（防误发；订阅号每天 1 次群发额度）。

## 头条号 / 百家号 / 企鹅号 / 雪球 → 一稿多发包
```bash
DEEPFOCUS_API_BASE=http://127.0.0.1:8300 python3 -m tools.syndicate.headline_pack [--date 2026-06-26]
```
产出 `syndicate_out/pack_<date>/`：`titles.txt`(5 个候选) / `body.md` / `card_1..4.png` / `backlink.txt` / `copy.txt`(标题+正文合一)。
平台后台新建图文 → 粘 `copy.txt` + 传 `card_*.png` → 发布。**不自动发布**（平台反垃圾严，秒封）。文末裸链=外链反哺百度权重。

## 建议
配 cron 每交易日收盘后（如 15:50）跑 `headline_pack` 自动生成包、`wx_mp_export` 推草稿箱，人工只做「挑标题 + 点发布」。
