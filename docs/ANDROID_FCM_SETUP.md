# Android FCM 实时提醒配置

代码已经支持 FCM 高优先级 data message；没有配置 Firebase 时，Android 仍会使用原生轮询兜底，不会导致 APK 无法启动。

## 1. Firebase Android 应用

在 Firebase 项目中新增 Android 应用，包名必须是：

```text
com.daocaijing.app
```

下载 `google-services.json`，放到：

```text
android/app/google-services.json
```

该文件包含客户端配置，构建时会被 Gradle 使用；不要把服务端 service account JSON 提交到 Git。

## 2. 后端发送凭据

给生产 FastAPI 进程配置以下任一项：

```bash
DEEPFOCUS_FIREBASE_SERVICE_ACCOUNT_FILE=/secure/path/firebase-service-account.json
# 或：
DEEPFOCUS_FIREBASE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
DEEPFOCUS_FIREBASE_PROJECT_ID=your-firebase-project-id  # 可省略，默认从 JSON 读取
```

后端依赖 `google-auth`，通过 Firebase HTTP v1 API 发送高优先级 data message。资讯入库后的现有 `dispatch_recall` 会自动扇出到 FCM；App 里选择的类型、关键词和“仅重要/全部”会同步到服务端。

### 四类资讯统一分发

快讯和文章由 DAO 事件桥接进入实时消息库；研报工作台、机构纪要流由后台
`recall-ingest` 每 75 秒增量接入同一消息库。因此四类内容共用同一套类型、关键词、
自选股和重要级别过滤。首次启动只建立历史基线，避免重启时把旧内容重复推送；如需
首次补发最新 N 条，可设置 `DEEPFOCUS_RECALL_INGEST_BACKFILL=N`。

同步周期可通过 `DEEPFOCUS_RECALL_INGEST_SECONDS` 调整（最小 20 秒）。若研报/纪要
源暂时不可用，任务会单独记录日志并在下一轮自动重试，不影响快讯和文章。

## 3. App 内开启

安装新 APK 后：

1. `更多 → 后台常驻提醒`，允许通知权限。
2. App 会打开系统电池优化列表；也可以点击 `电池后台策略` 再次打开。
3. 请在系统设置里把稻财经设为“不受限制”（应用不申请受 Google Play 限制的
   `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` 特殊权限）。

FCM 是“秒级目标”的实时通道，但 Android/厂商网络策略无法承诺绝对零延迟；原生轮询继续作为断网、Firebase 配置缺失或消息通道异常时的兜底。

FCM 对临时 429/5xx 和网络抖动会自动做两次有界重试；Android FCM 与 90 秒轮询使用
稳定去重键，避免同一条研报/纪要同时走两条通道时弹窗两次。
