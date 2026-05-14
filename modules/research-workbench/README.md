# 知识星球文件下载器

这个脚本只使用你已登录账号本来可以访问和下载的文件。首次运行会打开一个 Chrome 窗口，请在里面完成知识星球登录；登录态会保存在当前目录的 `.zsxq-browser-profile`。

```bash
npm install
npm run download
```

只列出文件，不下载：

```bash
node zsxq-downloader.js --group 88888142214212 --list-only
```

按上传时间排序、只下载 PDF：

```bash
node zsxq-downloader.js --group 88888142214212 --sort by_create_time --ext pdf
```

输出目录默认是 `downloads`，里面会生成 `manifest.json` 和 `files.csv`，用于断点续跑和查重。

按星球标签下载主题附件，例如“海外投行报告”：

```bash
node zsxq-downloader.js --group 88888142214212 --tag 海外投行报告 --ext pdf --out downloads/海外投行报告
```

先小批量验证：

```bash
node zsxq-downloader.js --group 88888142214212 --tag 海外投行报告 --ext pdf --limit 20 --out downloads/海外投行报告
```

## 可视化工具

启动本地工具：

```bash
npm run tool
```

打开 `http://127.0.0.1:3927`，可以按标签模糊搜索文件，勾选搜索结果后只下载选中的文件。

## 使用 Cookie

更推荐把 Cookie 放在本地文件里，不要贴到聊天或命令历史中：

```bash
printf '%s\n' '你的 Cookie 字符串' > cookie.txt
node zsxq-downloader.js --group 88888142214212 --cookie-file cookie.txt --list-only --max-pages 1
```

也支持环境变量：

```bash
ZSXQ_COOKIE='a=b; c=d' node zsxq-downloader.js --group 88888142214212 --list-only --max-pages 1
```

Cookie 可以从浏览器开发者工具的 Network 面板里复制：登录 `https://wx.zsxq.com` 后，打开对应的 `api.zsxq.com/v2/...` 请求，在 Request Headers 里复制 `Cookie` 的值。

如果你已经从浏览器复制了完整 curl，也可以保存为本地文件，脚本会自动提取 `Cookie` 和 `x-aduid`：

```bash
pbpaste > zsxq.curl
node zsxq-downloader.js --group 88888142214212 --curl-file zsxq.curl --list-only --max-pages 1
```
