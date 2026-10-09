# 稻草财经小程序壳

web-view 承载 `https://daocaijing.com` 移动 H5，登录、快讯、深度文章、机构纪要、投行研报、AI 对话全部由 H5 提供；壳负责微信入口、分享卡片与深链。

## 目录结构

```
miniprogram/
├── project.config.json        # 开发者工具项目配置（appid 上线前替换为正式 AppID）
├── app.json / app.js / app.wxss
├── sitemap.json
└── pages/webview/             # 唯一页面：web-view + 加载失败重试
```

## 深链约定

页面支持 `?path=` 携带站内路径（仅限本站路径，防外域跳转）：

```
/pages/webview/index?path=%2Farticle%2F123
/pages/webview/index?path=%2Flogin
```

web-view 会自动用 H5 的 `document.title` 同步导航栏标题，无需逐页配置。

## 上架步骤（需要小程序账号管理员操作）

1. **注册小程序账号**：https://mp.weixin.qq.com 注册，主体须为**企业 / 个体工商户**（个人主体无 web-view 权限，此方案不可行）。
2. **配置业务域名**：MP 管理后台 → 开发 → 开发管理 → 开发设置 → 业务域名 → 下载校验文件（`XXXX.txt`），交给运维放入服务器 `/var/www/wechat-verify/`（nginx 已配好该通道，文件落到该目录即生效，无需改配置），校验通过后填入 `daocaijing.com`。
3. **导入工程**：微信开发者工具 → 导入本目录 → 填入正式 AppID（替换 `project.config.json` 的 `touristappid`）。
4. **上传提审**：上传代码 → 版本管理提交审核。类目建议「工具 > 信息查询/效率」，备注说明内容为自有网站嵌载；若审核以财经资讯类目要求资质被拒，再评估调整内容入口或补充资质。

## 提审前检查

- [ ] `project.config.json` appid 已替换
- [ ] 业务域名 daocaijing.com 已校验通过
- [ ] 真机预览：登录 → 快讯流 → 文章 → 研报 → AI 对话全链路可用
- [ ] 分享卡片（og-cover.png）正常显示

## 已知边界

- iOS 端 H5 内的虚拟支付受限，会员购买建议保留兑换码/安卓通道。
- H5 侧未接微信 JS-SDK（无公众号签名），分享卡片固定为平台级，不带具体文章路径。
- 后续增强：订阅消息（研报更新提醒）、深链直跳具体内容（需 H5 侧接 postMessage 桥）。
