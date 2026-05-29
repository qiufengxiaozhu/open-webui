---
name: openApi故障分析
description: 分析 luoshu-server OpenAPI 错误日志，覆盖全局中间件（鉴权/限流/License）→ 参数校验 → 任务入队（队列限流/锁）→ 源文件下载 → 文件校验（MIME/大小/加密/内容限制）→ 转换执行（Word/Sheet/PPT/PDF/图片/OFD/水印，含6+转换引擎）→ 回调通知 → 下载全链路。当用户提到 OpenAPI 报错、转换失败、word转pdf错误、任务超时、ResCode、ConvertErrCode、回调失败、下载失败、队列拥塞、ServerBusy、MIME不匹配、加密文档、水印失败、Java转换错误时触发。
disable-model-invocation: true
---

# lse-openapi-error-log：OpenAPI 错误日志分析（完整版）

## 用户应提供的信息

分析 OpenAPI 错误日志时，用户需尽可能提供以下信息，以便快速定位问题：

| 优先级 | 信息 | 说明 | 示例 |
|--------|------|------|------|
| **必须** | 错误发生的大概时间 | 用于缩小日志搜索范围（精确到分钟最佳） | `2026-05-22 14:30` 左右 |
| **强烈建议** | taskId | OpenAPI 任务的唯一标识，可从提交响应或回调中获取 | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` |
| **强烈建议** | 错误现象描述 | 如"回调收到 FAIL"、"下载报错"、"一直 IN_QUEUE" 等 | — |
| 建议 | 调用的接口路径 | 用于区分 /convert、/content/ops、/merge 等不同路由 | `/publicapi/v1/convert` |
| 建议 | 请求参数概要 | 源文件类型、目标格式、是否带水印等 | `docx → pdf，有平铺水印` |
| 建议 | 回调收到的内容 | 回调 body 中的 code、detail.msg | `code: "ConvertFailNotify"` |
| 有则提供 | docId | 使用 docId 方式提交时的文档 ID | — |
| 有则提供 | 错误截图/HTTP 响应 | 接口返回的完整 JSON | — |

> **为什么时间很重要**：OpenAPI 日志量通常很大，不提供时间范围将导致搜索范围过广，难以高效定位。即使是大概时间（如"上午 10 点左右"）也能极大缩小范围。

---

## 按 taskId / docId 定位问题

### 方法一：按 taskId 全链路串联（最常用）

taskId 是贯穿 OpenAPI 请求全链路的唯一标识，从入队到回调、下载的所有日志都会包含该 ID。

```bash
LOG_DIR=/opt/zdocs/luoshu_log

# 1. 全节点搜索 taskId 的所有日志（按时间排序），得到完整时间线
rg '<taskId>' $LOG_DIR/*/{combined,error,java}*.log* | sort

# 2. 根据时间线判断阶段：
#    - 有 [convertReqHandler] → 请求已到入口
#    - 有 [Task Server] Receive Task → TaskServer 已接收
#    - 有 [LocalConvertServiceImp] Download → 源文件下载阶段
#    - 有 [LocalConvertServiceImp] Done the execution → 转换完成（看 code）
#    - 有 [ConvertWorker] Result → 最终结果码
#    - 有 [AbstractOpenService] notify → 回调发送

# 3. 如果搜不到任何结果：
#    - taskId 是否正确？
#    - 日志是否是对应时间段的？（日志文件可能按日期滚动）
#    - 是否在所有节点都搜索了？
```

**taskId 日志时间线示例**：

```
14:30:01  [convertReqHandler] ... taskId=abc123 ... code=Ok          ← ③ 入队成功
14:30:02  [Task Server] Receive Task : [abc123 / CONVERT]            ← TaskServer 接收
14:30:03  [LocalConvertServiceImp] Download of task [abc123] done in 850ms  ← ④ 下载完成
14:30:03  [LocalConvertServiceImp] tid:abc123 fileSize is 2048576 B  ← 文件大小
14:30:08  [LocalConvertServiceImp] Done the execution ... [abc123] in 5100ms with code [200]  ← ⑥ 转换完成
14:30:08  [ConvertWorker] Result of Conversion task [abc123]: [1299]  ← 最终成功
14:30:08  [AbstractOpenService] notify ... taskId=abc123 ... Success  ← ⑦ 回调成功
```

### 方法二：按 docId 定位（docId 方式提交时）

当使用 docId 方式（而非 fileUrl）提交任务时，可通过 docId 搜索：

```bash
# docId 在 NewDocServer 日志中出现（入口层），也可能在 TaskServer 日志中出现
rg '<docId>' $LOG_DIR/NewDocServer_*/{combined,error}*.log* | sort
rg '<docId>' $LOG_DIR/TaskServer_*/{combined,error}*.log* | sort
```

> 注意：docId 方式下，日志中 taskId 仍然是主要关联 ID。先通过 docId 找到对应的 taskId，再按 taskId 串联全链路。

### 方法三：按时间段扫描（无 taskId 时）

当用户只能提供大概时间而无 taskId 时：

```bash
# 1. 搜索该时间段内的所有 OpenAPI 入口日志
rg '\[convertReqHandler\]|\[modelOpHandler\]' $LOG_DIR/NewDocServer_*/combined*.log* | rg '14:30'

# 2. 搜索该时间段内的所有错误
rg 'error|fail|Error|FAIL' $LOG_DIR/*/error-*.log* | rg '14:30'

# 3. 搜索该时间段内的所有转换结果
rg '\[ConvertWorker\] Result' $LOG_DIR/TaskServer_*/combined*.log* | rg '14:30'

# 4. 也可使用分析脚本自动化（见"脚本辅助"章节）
```

---

## 请求全链路

```
⓪ 全局中间件
   HTTP 请求 → HeadersCheck（必填头检查）→ Nonce 限流（10s 内同 nonce 最多 1 次）
   → 校验失败：HTTP 401（缺头）或 HTTP 429（限流）

① 鉴权与许可
   → OpenServiceAuth_ls19：HMAC 签名校验（Authorization 三段式 repoId:appId:token）
   → openLicenseCheck_ls19：License 存在性/有效性/过期检查
   → openPortalAppQuotaMiddleware_ls19：SaaS 应用 Quota 检查
   → 校验失败：HTTP 401（鉴权）或 HTTP 200 + code（License/Quota）

② 参数校验
   → 路由级中间件链（checkParamCallback → generalParamCheck → 路由专用校验）
   → 参数合法性：filename/fileUrl/callback/水印/toPicOptions/imgToPdfOptions
   → 白名单校验：fileUrl host / callback host / downloadUrl host
   → 校验失败：HTTP 412 + ResCode

③ 任务入队
   → OpenServiceFactory 路由选择（HTTP_CONVERT / MODEL_OPERATION / FIXEDWATERMARK_CONVERT）
   → AbstractOpenService.addTask_ls19：
       a) isOpenServiceSupport_ls19：转换类型支持检查 + checkParamsSupport_ls19 二次深度校验
       b) Redis 队列锁获取（20s 超时）
       c) rateLimitCheck_ls19：活跃任务数 vs maxActiveTask + CPU 并发 License
       d) taskService.addTask_ls19（Bull 入队）
   → 入队成功：返回 { taskId, code: "Ok", detail: { taskStatus: "IN_QUEUE" } }
   → 入队失败：ServerBusy(506) / TaskQueueCongestion(505) / NotSupportTask(513)

④ 源文件下载（TaskServer 异步）
   → ConvertFileService → RemoteContentImpl（fileUrl HTTP 下载，超时 10s，无重试）
   → 或 ContentService.copyContentToPath（docId 本地存储拷贝）
   → 下载失败：CONVERT_DOWNLOAD_ERROR(1001)
   → 解密失败：CONVERT_DOWNLOAD_ORIGIN_FILE_DECRYPT_ERROR(1004)

⑤ 文件校验
   → LocalConvertSrvImp.validate_ls19：
       a) 空文件检测（size=0）
       b) 文件大小上限检查（按转换对配置，Word/Sheet 50MB、PPT 100MB 等）
       c) OOXML 最小文件检测（< 1KB）
       d) MIME 类型检测（Java FileTypeUtil）
       e) 加密文档检测（OOXML/ODF）
       f) 扩展名与实际类型修正
   → 校验失败：FILE_TOO_LARGE(413) / FILE_INVALID_MIMETYPE(415) / INVALID_PASSWORD(491) 等

⑥ 转换执行
   → ConvertorFactory 选择转换器 → IConvertor.convert_ls19

   【/convert 接口 — 直接转换路径】
   Word→PDF:
     ├── WebPrintConvertor       （PUPPETEER 引擎，配置 toPdf.text=pptr）
     ├── CanvasConvertor         （CANVAS 引擎，非 API 任务）
     ├── Doctype2FixedWatermarkConvertor  （CANVAS + API_TASK，或 as1 + 无修订）
     └── AsposeConvertor         （有 revisions 时的 as1 引擎）
   Sheet→PDF:
     ├── CanvasConvertor（CANVAS 引擎）→ 走 MODEL_OPERATION(SaveAs)
     └── AsposeConvertor（ASPOSE 引擎）
   PPT→PDF: Slide2PdfConvertor → JavaSAConvertor（独立 Java 进程）
   图片→PDF: Img2PdfConvertor（Java plugin）
   PDF→图片: PDF2PICConvertor
   OFD 相关: OFD2PDFConvertor / PDF2OFDConvertor
   水印: PDFWatermarkConvertor / OFDWatermarkConvertor（Java plugin）
   合并: PDFMergeConvertor
   通用: CommonJavaConvertor（Java plugin 框架）

   【/content/ops 接口 — ModelOp 两步路径（word→PDF 不加水印场景）】
   step1: CONVERT 任务 — Word/Sheet → JSON（内部草稿格式，uploadToDb: true）
       → LocalConvertServiceImp → CLConvertor（fork OOXML 转换二进制）
       → 完成后触发 [HttpModelOpServiceImpl] "get draft success"
   step2: MODEL_OP 任务 — 对 JSON 执行 SaveAs/水印等操作
       → ModelServiceWorker → ModelManager.processCanvasActionSaveAs
           → 根据文件大小决定执行方式：
               ├── 子进程（大文件/默认）: fork CanvasProcess.js → CanvasModelService
               └── 线程（小文件+canvasInThread=true）: ThreadJob → CanvasModelService
           → CanvasModelService 用 SwPrintController（word）/ ScPrintController（sheet）渲染 PDF
       → 完成后回调调用方

⑦ 回调通知
   → AbstractOpenService.notify_ls19：axios POST 到 callbackUrl（超时 5s，无重试机制）
   → 成功回调：code=TaskSuccessNotify/ConvertSuccessNotify，含 contentId
   → 失败回调：code=TaskFailNotify/ConvertFailNotify，含错误信息
   → 同时写入 Redis 任务状态（TTL=downloadExpireInMinute，默认 60 分钟）

⑧ 下载
   → 调用方收到成功回调后，用 taskId + contentId 调用下载接口
   → downloadResult 校验 taskId 有效性、归属权限、contentId 存在性
   → 从存储（FS/DBOBJECT/GRIDFS）取文件流返回
```

> `[openApiStatistics]` 是统计中间件本身出错的日志，**不代表请求失败**，不能用于阶段判断。

---

## 阶段定位：根据现象判断

| 现象 | 所在阶段 | 关键日志前缀 / HTTP 状态 |
|------|---------|--------------------------|
| HTTP 401 响应，无 body 或 body 含 errorMsg | ⓪① 全局中间件/鉴权 | 无固定前缀，看 HTTP status |
| HTTP 429 "Too many requests" | ⓪ Nonce 限流 | 无前缀 |
| HTTP 412 + ResCode，无 taskId | ② 参数校验 | 无固定前缀，看 ResCode |
| HTTP 200 + code 非 Ok，无 taskId | ①②③ 鉴权/License/入队 | `[convertReqHandler]` |
| 接口返回 ServerBusy / TaskQueueCongestion | ③ 任务入队 | `[AbstractOpenConvertService]` |
| 有 taskId，queryTaskStatus 返回 FAIL | ④⑤⑥ 下载/校验/转换 | `[LocalConvertServiceImp]` `[Text2PdfConvertor]` 等 |
| 有 taskId，callback 收到 FAIL | ④⑤⑥ 下载/校验/转换 | 同上 |
| 有 taskId，callback 未收到（也无 SUCCESS） | ⑦ 回调发送失败 | `[AbstractOpenService] task notify with error` |
| 收到 SUCCESS 回调后下载报错 | ⑧ 下载 | `[downloadResult]` |
| ModelOp 路径（/content/ops）失败 | ①②③⑥ | `[modelOpHandler]` `[HttpModelOpServiceImpl]` `[ModelServiceWorker]` `[OpenModelWorker]` `[ModelManager]` `[CanvasModelService]` |
| queryTaskStatus 返回 InvalidTaskId | ⑧ 任务过期 | Redis key 已过期（默认 60min TTL） |

---

## 日志搜索

日志文件说明：
- `combined-*.log`：所有级别日志（info/warn/error）
- `error-*.log`：仅 error 级别
- `java*.log`：Node 进程通过 node-java 调用 Java 库时的输出（如加水印、格式转换），与 Node 日志分开写入

日志路径默认为 `/opt/zdocs/luoshu_log/*/`，根据实际部署情况调整（下文以 `$LOG_DIR` 代替）。

```bash
LOG_DIR=/opt/zdocs/luoshu_log

# ═══════════════════════════════════════
# 通用：按 taskId 串联全链路（必须搜所有节点、所有日志文件）
# ═══════════════════════════════════════
rg '<taskId>' $LOG_DIR/*/{combined,error,java}*.log* | sort

# ═══════════════════════════════════════
# ⓪① 全局中间件 / 鉴权层
# ═══════════════════════════════════════
# HMAC 鉴权失败
rg 'verify token fail|Token format is invalid|repoId not enabled|authorization timestamp expired|Secret of public api is null|Public API is disabled' $LOG_DIR/*/combined*.log*
# License 检查失败
rg 'License not found|License is invalid|License has expired' $LOG_DIR/*/combined*.log*
# SaaS Quota 失败
rg 'Application not found|Application has expired|Invokable.*exhausted' $LOG_DIR/*/combined*.log*

# ═══════════════════════════════════════
# ② 参数校验（HTTP 412 返回）
# ═══════════════════════════════════════
# 水印参数校验
rg 'WMPicTypeNotSupport|WMPicIsNotSupport|WMTextParamIsNull|WMFontSizeParamIsNull|WMPositionError|WMPicUrlOrPicNameIsNull' $LOG_DIR/*/combined*.log*
# 白名单校验
rg 'FileUrlNotAllowed|CallbackUrlNotAllowed|DownloadUrlNotAllowed' $LOG_DIR/*/combined*.log*

# ═══════════════════════════════════════
# ③ 任务入队
# ═══════════════════════════════════════
rg '\[convertReqHandler\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[AbstractOpenConvertService\]' $LOG_DIR/*/{combined,error}*.log*
rg 'get queue lock fail|maxActiveTask|concurrent exceeds license limit|queue is full' $LOG_DIR/*/combined*.log*

# ═══════════════════════════════════════
# ④ 源文件下载
# ═══════════════════════════════════════
rg '\[RemoteContentImpl\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[LocalConvertServiceImp\] Download' $LOG_DIR/*/{combined,error}*.log*
rg 'download error|CONVERT_DOWNLOAD_ERROR|download file error' $LOG_DIR/*/{combined,error}*.log*
rg '0 bytes|is too small|is invalid' $LOG_DIR/*/combined*.log*

# ═══════════════════════════════════════
# ⑤ 文件校验
# ═══════════════════════════════════════
rg '\[MimeGroupUtil\]|\[MimeTypes\]' $LOG_DIR/*/{combined,error}*.log*
rg 'Invalid File mime type|no supported mime type|Password Protected|UNSUPPORTED_ENCRYPTION' $LOG_DIR/*/{combined,error}*.log*
rg 'is larger than the max size|FILE_TOO_LARGE|CONTENT_LIMIT' $LOG_DIR/*/{combined,error}*.log*
rg 'correct mime type should be|MIME_TYPE_MODIFIED' $LOG_DIR/*/combined*.log*

# ═══════════════════════════════════════
# ⑥ 转换执行
# ═══════════════════════════════════════
# Word→PDF
rg '\[Text2PdfConvertor\]' $LOG_DIR/*/{combined,error}*.log*
# Puppeteer
rg '\[WebPrintConvertor\]' $LOG_DIR/*/{combined,error}*.log*
# Canvas
rg '\[CanvasConvertor\]' $LOG_DIR/*/{combined,error}*.log*
# 带水印链式转换
rg '\[Doctype2FixedWatermarkConvertor\]' $LOG_DIR/*/{combined,error}*.log*
# Aspose
rg '\[AConvertor\]' $LOG_DIR/*/{combined,error}*.log*
# CL/OOXML 二进制
rg '\[CLConvertor\]|\[ClConvertor\]' $LOG_DIR/*/{combined,error}*.log*
rg 'ExitCode.*errCode' $LOG_DIR/*/combined*.log*
rg 'time to cancel task' $LOG_DIR/*/combined*.log*
# Sheet→PDF
rg '\[Sheet2PdfConvertor\]' $LOG_DIR/*/{combined,error}*.log*
# PPT→PDF (Java Standalone)
rg '\[JavaSAConvertor\]|\[Slide2PdfConvertor\]' $LOG_DIR/*/{combined,error}*.log*
rg 'java standalone convertor is busy' $LOG_DIR/*/combined*.log*
# 图片→PDF
rg '\[Img2PdfConvertor\]' $LOG_DIR/*/{combined,error}*.log*
# PDF 水印
rg '\[PDFWatermarkConvertor\]|\[OFDWatermarkConvertor\]' $LOG_DIR/*/{combined,error}*.log*
# 通用 Java 转换
rg '\[CommonJavaConvertor\]' $LOG_DIR/*/{combined,error}*.log*
# 文档合并
rg '\[PDFMergeConvertor\]' $LOG_DIR/*/{combined,error}*.log*

# ═══════════════════════════════════════
# ⑥ ModelOp 两步流程
# ═══════════════════════════════════════
# step1 成功标志
rg '\[HttpModelOpServiceImpl\].*get draft success' $LOG_DIR/*/combined*.log*
# step1 失败
rg '\[LocalConvertServiceImp\]|\[CLConvertor\]' $LOG_DIR/*/{combined,error}*.log*
# step2 ModelOp 执行
rg '\[ModelServiceWorker\]|\[OpenModelWorker\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[ModelManager\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[CanvasModelService\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[CanvasProcess\]' $LOG_DIR/*/{combined,error}*.log*
# step2 清理失败
rg 'clearTempData fail' $LOG_DIR/*/{combined,error}*.log*
# 超时/kill
rg 'is killed because of timeout|failed to be killed|Error is returned by Worker' $LOG_DIR/*/{combined,error}*.log*
# ModelOp 入口拒绝
rg 'model op busy|model op fail|model op error' $LOG_DIR/*/{combined,error}*.log*

# ═══════════════════════════════════════
# ⑦ 回调
# ═══════════════════════════════════════
rg '\[AbstractOpenService\].*notify' $LOG_DIR/*/{combined,error}*.log*
rg 'failed to notify with wrong taskCtx' $LOG_DIR/*/{combined,error}*.log*
rg 'notify res fail' $LOG_DIR/*/combined*.log*

# ═══════════════════════════════════════
# ⑧ 下载
# ═══════════════════════════════════════
rg '\[downloadResult\]' $LOG_DIR/*/{combined,error}*.log*

# ═══════════════════════════════════════
# Java 层（node-java / Java Standalone）
# ═══════════════════════════════════════
rg 'ERROR|Exception|OutOfMemory' $LOG_DIR/*/java*.log*
rg '\[JavaSAConvertor\] stdout\|stderr' $LOG_DIR/*/combined*.log*

# ═══════════════════════════════════════
# 任务生命周期
# ═══════════════════════════════════════
rg '\[TaskStatusServiceImpl\]' $LOG_DIR/*/combined*.log*
rg 'TimeoutError|Job.*is failed|CONVERSION_TIMEOUT' $LOG_DIR/*/{combined,error}*.log*
rg 'task is running' $LOG_DIR/*/combined*.log*
```

---

## ResCode 速查（OpenController 层，HTTP 同步返回）

> 定义文件：`src/docsserver/services/openservice/OpenService.ts`
> 响应中 code 字段是**枚举名字符串**（如 `"FilenameIsNull"`），不是数字。

### ⓪ 全局中间件错误（非 ResCode 格式）

| HTTP 状态 | 触发条件 | 响应格式 |
|-----------|---------|---------|
| **401** | 缺少 `zOffice-auth-type`/`zOffice-message-nonce`/`timeStamp` 头 | `Header_{name}_IsNull` (406) |
| **429** | 同一 `message-nonce` 10 秒内重复请求 | `Too many requests.` |

### ① 鉴权错误（HTTP 401）

> 鉴权失败 body 格式与业务接口不同（message + errorMsg），非标准 formatRes。

| ResCode 名称 | 值 | 触发条件 |
|-------------|------|---------|
| `InvalidAuthHeader` | 400 | HMAC 签名不匹配 |
| `InvalidAuthRepoID` | 401 | repo 配置不存在或未启用 PublicAPI |
| `InvalidAuthTimestamp` | 402 | 时间戳与服务器差 > 30s |
| `TokenIsInvalid` | 403 | Authorization 不是 `repoId:appId:token` 三段格式 |
| `AuthHeaderIsNull` | 404 | 无 Authorization 头 |
| `PublicApiIsDisable` | 464 | PublicAPI 未启用（repo 配置级） |
| `SecretIsNull` | 465 | repo 的 PublicAPI secret 未配置 |

> 签名算法：`createDesignatedToken_ls19(authType, timestamp, nonce, secret, rawBody)`

### ① License/Quota 错误（HTTP 200 + code）

| ResCode 名称 | 值 | 触发条件 | 备注 |
|-------------|------|---------|------|
| `LicenseNotFound` | 407 | 无 License | Box repo 豁免 |
| `InvalidLicense` | 408 | License 内容无效 | |
| `LicenseIsExpire` | 409 | License 已过期 | |
| `AppIsExpired` | 472 | SaaS 应用已过期 | 仅 `isSass()=true` 时生效 |
| `ApplicationNotFound` | 474 | SaaS 应用不存在 | 仅 `isSass()=true` 时生效 |

### ② 参数校验错误（HTTP 412 + ResCode）

#### 通用参数

| ResCode 名称 | 值 | 触发条件 |
|-------------|------|---------|
| `FilenameIsNull` | 410 | filename 参数为空 |
| `TargetFilenameIsNull` | 411 | targetFilename 为空（/convert 接口） |
| `CallbackIsNull` | 414 | callback URL 为空 |
| `ConflictSourceInfo` | 415 | fileUrl 与 docId 同时传入 |
| `SourceInfoIsNull` | 416 | fileUrl 和 docId 均为空 |
| `RepoIdIsNull` | 417 | docId 方式时 repoId 为空 |
| `DocIdIsNull` | 418 | docId 方式时 docId 为空 |
| `FileUrlNotAllowed` | 419 | fileUrl host 不在白名单（基准 host = repoConf.editContext） |
| `CallbackUrlNotAllowed` | 420 | callback host 不在白名单（Box repo 豁免） |
| `DocTypeNotSupport` | 421 | 文件类型不支持该转换 |
| `InvalidArgument` | 422 | uniqueId 有但无 tiledWatermark 等 |
| `ParamTypeError` | 432 | 参数类型错误（如数字字段传字符串） |
| `ParamOutOfRange` | 433 | 参数超出允许范围 |
| `NotSupportOptions` | 434 | 选项组合不支持（如 Excel→PDF Aspose 无 sheetIndex） |

#### 水印参数

| ResCode 名称 | 值 | 触发条件 |
|-------------|------|---------|
| `WMPositionError` | 450 | position 不在 WaterMarkPosition 枚举内 |
| `WMTextParamIsNull` | 451 | 固定文字水印 text 为空 |
| `WMFontSizeParamIsNull` | 452 | fontsize 为空 |
| `WMFontColorParamIsNull` | 453 | fontcolor 为空 |
| `WMFontParamIsNull` | 454 | font 为空 |
| `WMPicUrlOrPicNameIsNull` | 455 | picUrl 有但 picName 无 |
| `WMPicTypeNotSupport` | 456 | picName 扩展名 ∉ {jpg,jpeg,png,bmp} |
| `WMPicIsNotSupport` | 457 | 需要 line1 但为空（uniqueId/PPT→PDF 场景） |

> **水印参数范围校验**：transparent ∈ [0,100]、picScale ∈ [0,200]、rotation ∈ [-360,360]、fontsize ∈ [1,72]
> **图片路由特殊限制**：纯图片水印 rotation 仅允许 {0,90,180,270}

#### 图片操作参数

| ResCode 名称 | 值 | 触发条件 |
|-------------|------|---------|
| `ImageOPNoParams` | 458 | /image/transform 缺少操作参数 |
| `PDF2PICNoParams` | 459 | PDF 转图片缺参数 |
| `LongPicTypeError` | 460 | longPicType 不支持（仅允许 MD/TD） |
| `TypeIsNullORInvalid` | 462 | /split type 参数无效 |
| `keywordIsNull` | 463 | /split keyword 为空 |
| `Text2PicLongPicTypeError` | 466 | Word 转图不允许 longPicType |
| `ImgToPdfInvalidPageSize` | 467 | pageSize ∉ {FIT_IMAGE, A4, A3} |
| `ImgToPdfInvalidOrientation` | 468 | orientation ∉ {portrait, landscape, auto} |
| `ImgToPdfInvalidMargin` | 469 | margin ∉ {none, narrow, wide} |

#### ModelOp 参数（/content/update、/content/query）

| ResCode 名称 | 值 | 触发条件 |
|-------------|------|---------|
| `NotSupportEmptyOps` | 427 | ops 空/非数组/长度<1 |
| `TooManyOps` | 428 | ops 长度 > 2 |
| `InvalidOpsWithEmptyOpActId` | 429 | 任一 op 缺 actId |
| `InvalidOpsWithSameActId` | 430 | ops 中 actId 重复 |
| `InvalidOpsWithWatermarkActId` | 431 | 水印类 actId 但 ops 长度不为 1 |
| `NotSupportModelOp` | 436 | 不支持的 ModelOp actId |

#### /merge 专用

| ResCode 名称 | 值 | 触发条件 |
|-------------|------|---------|
| `HasDifferentType` | 437 | 合并的文件类型不一致 |
| `WrongPage` | 438 | 页码范围错误 |
| `FileListIsNull` | 439 | fileList 为空 |
| `DownloadUrlIsNull` | 440 | fileList 项缺 downloadUrl |
| `DownloadUrlNotAllowed` | 441 | downloadUrl host 不在白名单 |
| `TooManyFiles` | 442 | 文件数量超限 |
| `SameNameButDifferentUrl` | 443 | 同名文件不同 URL |

#### /protect 专用

| ResCode 名称 | 值 | 触发条件 |
|-------------|------|---------|
| `ProtectionActIsNull` | 445 | protectionAct 为空 |
| `ProtectionActNotAllowed` | 447 | protectionAct 不在允许列表 |
| `InvalidSecret` | 448 | rand32 密钥未配或长度 ≠ 32 |
| `DecryptFail` | 449 | AES-256-ECB 解密 password 失败 |

### ③ 任务入队错误（HTTP 200 + ResCode，detail.taskStatus=FAIL）

| ResCode 名称 | 值 | 触发条件 |
|-------------|------|---------|
| `TaskQueueCongestion` | 505 | 活跃 OpenAPI 任务数 ≥ maxActiveTask |
| `ServerBusy` | 506 | Redis 队列锁获取失败 / addTask 返回 null / CPU License 超限 |
| `NotSupportTask` | 513 | 转换类型不在 supportConvertMap / checkParamsSupport 抛错 |
| `UriNotFound` | 435 | 请求路径无法匹配已知校验函数 |

> **maxActiveTask 计算公式**：`taskServerSum × capabilityIndex × convertMaxParallels`
> 默认：taskServerSum × 0.5 × 16
> 另外 `ConcurrentCPULicenseUtils.isExceedsLimit_ls19()` 也会触发 TaskQueueCongestion。

### ⑦⑧ 回调/下载阶段错误码

| ResCode 名称 | 值 | 用途 |
|-------------|------|------|
| `ConvertSuccessNotify` | 507 | convert 成功回调 code |
| `ConvertFailNotify` | 508 | convert 失败回调 code |
| `TaskSuccessNotify` | 509 | ModelOp 成功回调 code |
| `TaskFailNotify` | 510 | ModelOp 失败回调 code |
| `TaskHandingNotify` | 511 | 任务处理中（Redis 状态，queryTaskStatus 可查到） |
| `InvalidTaskId` | 423 | taskId 无效/已过期（TTL 默认 60min） |
| `AccessOtherRepoIsNotAllowed` | 424 | 下载时 userId 与任务归属不匹配 |
| `ContentIdError` | 425 | contentId 对应文件不存在 |
| `ContentIdIsNull` | 426 | 下载接口未传 contentId |
| `DownloadErr` | 502 | 下载通用错误 |
| `UnknownErr` | 500 | 未知错误 |
| `TaskTimeout` | 503 | 任务超时 |

---

## ConvertErrCode 速查（TaskServer 转换层）

> 定义文件：`src/taskserver/tasks/conversion/common/CommonTypes.ts`

### 基础

| Code | 值 | 含义 |
|------|----|------|
| `NOERR` | 0 | 无错误 |
| `SUB_UNKNOWN` | 100 | 子转换器未知错误 |
| `CONVERSION_DONE` | 200 | 转换完成 |
| `ACCEPTED` | 202 | 已接受（异步） |

### 文件/格式问题（4xx）

| Code | 值 | 含义 | 可重试 |
|------|----|------|--------|
| `ERROR_CONTAIN_SVM_FILE` | 411 | 含 SVM 文件 | 否 |
| `ERROR_CONTAIN_ASPOSE_RECORD` | 412 | 含 EMF/SmartArt/Chart 需二次转换 | 否 |
| `FILE_TOO_LARGE` | 413 | 文件过大 | 否 |
| `FILE_INVALID_MIMETYPE` | 415 | MIME 类型无效 | 否 |
| `INVALID_PASSWORD` | 491 | 密码错误/加密文档 | 否 |
| `UNSUPPORTED_ENCRYPTION` | 492 | 不支持的加密方式（ODF） | 否 |
| `SOFFICE_BUSY` | 493 | Symphony 实例全忙 | **是** |
| `SOFFICE_RUNTIME_ERROR` | 495 | Symphony 运行时错误/崩溃/超时 | **是** |
| `SOFFICE_UNAVILABLE` | 496 | Symphony 连接不可用 | **是** |
| `SOFFICE_ILLEGAL_ARGUMENT` | 497 | Symphony 非法参数 | 否 |
| `CONVERT_NOT_SUPPORTED` | 501 | 该格式转换不支持 | 否 |
| `UNKNOWN` | 520 | 未知错误 | 否 |

### CL/OOXML 二进制转换（521-539）

| Code | 值 | 含义 | 可重试 |
|------|----|------|--------|
| `CL_TIMEOUT` | 521 | CL 转换超时（默认 5min） | **是** |
| `IO_EXCEPTION` | 522 | IO 异常 | 否 |
| `SINGLE_PAGE_OVERTIME` | 523 | 单页渲染超时 | **是** |
| `CL_DOWNLOAD_ERROR` | 524 | CL 下载错误 | 否 |
| `PATH_UNACCESSED` | 525 | 路径不可访问 | 否 |
| `MIME_TYPE_MODIFIED` | 526 | 实际 MIME 类型与扩展名不符 | 否 |
| `CL_UNKNOWN` | 527 | CL 未知错误 | 否 |
| `CL_EMPTY_SLIDE` | 528 | 空幻灯片 | 否 |
| `CORRUPTED_FILE` | 529 | 文件损坏 | 否 |

### 内容限制（530-539）

| Code | 值 | 含义 | 默认限制 |
|------|----|------|----------|
| `SPREADSHEET_FILE_SIZE` | 530 | 表格文件过大 | — |
| `SPREADSHEET_CELL_NUMBER` | 531 | 单元格数超限 | 6,000,000 |
| `SPREADSHEET_ROW_NUMBER` | 532 | 行数超限 | 1,048,576 |
| `SPREADSHEET_COL_NUMBER` | 533 | 列数超限 | 16,384 |
| `SPREADSHEET_FORMULA_NUMBER` | 534 | 公式单元格超限 | 6,000,000 |
| `DOCUMENT_EXCEED_PAGE_COUNT` | 535 | 文档页数超限 | 1,000 |
| `DOCUMENT_EXCEED_PAGE_CHARACTER` | 536 | 文档字符数超限 | 2048 KB |
| `PRESENTATION_SLIDE_NUMBER` | 537 | 幻灯片数超限 | 300 |
| `PRESENTATION_OBJECT_NUMBER` | 538 | 演示对象数超限 | 3,000 |
| `PRESENTATION_SYM_TOOLARGE` | 539 | 演示符号过大 | — |

> 以上限制来自 Java `conversion-config.json`，可通过配置修改。

### 不支持特性（6xx-9xx）

| 范围 | 类型 | 说明 |
|------|------|------|
| 601-616 | Word | UNSUPPORT_FEATURE_WORD_* (各种 Word 不支持特性) |
| 701-711 | Sheet | UNSUPPORT_FEATURE_SHEET_* (各种 Sheet 不支持特性) |
| 801-814 | PPT | UNSUPPORT_FEATURE_PRES_* (各种 PPT 不支持特性) |
| 900 | ODF | UNSUPPORT_FEATURE_ODF |

> 不支持特性为**警告**（非致命），转换可能仍成功，最终码为 `CONTENT_UNSUPPORTED`(906)。

### 聚合错误码

| Code | 值 | 含义 | 聚合来源 |
|------|----|------|----------|
| `CONTENT_LIMIT` | 901 | 内容超限 | 530-539 |
| `CONVERSION_FOTMAT_ERR` | 902 | 格式错误 | 415, 529 |
| `CONVERSION_SOFFICE_ERR` | 903 | Symphony 错误 | 493, 495, 496, 497 |
| `CONVERSION_CL_ERR` | 904 | CL 错误 | 521-528 |
| `CONVERSION_UNKNOWN` | 905 | 转换未知错误 | 100, 520 |
| `CONTENT_UNSUPPORTED` | 906 | 含不支持特性（警告，仍可能成功） | 601-814, 900 |

### 基础设施错误（1001+）

| Code | 值 | 含义 |
|------|----|------|
| `CONVERT_DOWNLOAD_ERROR` | 1001 | 源文件下载失败（含 0 字节文件） |
| `CONVERT_UPLOAD_ERROR` | 1002 | 转换结果上传存储失败 |
| `CONVERT_INVALID_MIME_ERROR` | 1003 | MIME 不一致警告 |
| `CONVERT_DOWNLOAD_ORIGIN_FILE_DECRYPT_ERROR` | 1004 | 远程源文件解密失败 |
| `CONVERT_CAD_ERROR` | 1005 | CAD 转换错误 |
| `CONVERT_CAD_TIMEOUT` | 1006 | CAD 超时 |
| `CONVERT_STANDALONE_BUSY` | 1011 | Java Standalone / Puppeteer 实例全忙（Redis 锁满） |
| `JAVA_STANDALONE_CONVERT_ERR` | 1012 | Java Standalone 进程转换错误（exit code=1） |
| `EXTRACT_TOO_MANY` | 1013 | 解压条目过多 |
| `DOWNLOAD_FILE_TOO_SMALL` | 1014 | 下载的 OOXML 文件过小（< 1KB） |

---

## 各转换器专项排查

### Word→PDF（Text2PdfConvertor）

日志标识：`[Text2PdfConvertor] [<tid>]`

**引擎选择规则**（按配置 `taskserver.exportDoc.toPdf.text`）：

| 配置引擎 | 任务类型 | 实际转换器 | 日志前缀 |
|---------|---------|-----------|---------|
| `pptr` (PUPPETEER) | 任意 | WebPrintConvertor | `[WebPrintConvertor]` |
| canvas (CANVAS) | 非 API_TASK | CanvasConvertor | `[CanvasConvertor]` |
| canvas (CANVAS) | API_TASK | Doctype2FixedWatermarkConvertor | `[Doctype2FixedWatermarkConvertor]` |
| `as1` (默认) | 无 revisions | Doctype2FixedWatermarkConvertor | `[Doctype2FixedWatermarkConvertor]` |
| `as1` (默认) | 有 revisions | AsposeConvertor | `[AConvertor]` |

**水印与转换路径**：

| 水印参数 | 类型 | 执行路径 |
|---------|------|---------|
| `tiledWatermark`（无 picUrl） | 平铺文字水印 | Canvas 直接渲染，一步完成 |
| `tiledWatermark`（有 picUrl） | 平铺图片水印 | Canvas→PDF 后 Java 加水印 |
| `msTextWatermark` | 固定位置文字水印 | Canvas→PDF 后 Java 加水印 |
| `msPicWatermark` | 固定位置图片水印 | Canvas→PDF 后 Java 加水印 |

> `uniqueId` 场景（固定水印复用 PDF 缓存）走独立服务，**不经过** `Text2PdfConvertor`。
> Redis PDF 缓存 TTL ≤ 30min 时不走缓存，重新转换。

**错误场景**：

| 场景 | 错误码 | 日志关键字 |
|------|--------|-----------|
| 引擎总 catch | `CONVERSION_UNKNOWN`(905) | `[Text2PdfConvertor]` |

#### WebPrintConvertor（Puppeteer 引擎）

| 场景 | 错误码 | 日志关键字 |
|------|--------|-----------|
| Redis 锁获取失败（并行满） | `CONVERT_STANDALONE_BUSY`(1011) | `[WebPrintConvertor]` |
| 页数超限（默认 100 页） | `FILE_TOO_LARGE`(413) | `totalPage > pageLimit` |
| Chrome 进程异常 | `UNKNOWN`(520) | `Cancel web print taskId` |
| Puppeteer launch 超时 | `UNKNOWN`(520) | 10s 硬编码超时 |
| PDF 生成超时 | `UNKNOWN`(520) | `page.pdf timeout` |

> Puppeteer 并行数 = `pptr.maxParallels × CAPABILITY_INDEX`，Redis 锁 TTL = `timeout/1000 + 20` 秒

#### CanvasConvertor

| 场景 | 错误码 | 日志关键字 |
|------|--------|-----------|
| ModelManager.processAction 失败 | `UNKNOWN`(520) | `[CanvasConvertor]` |
| 任务被取消 (interrupt) | `CONVERSION_UNKNOWN`(905) | `[CanvasConvertor]` |

#### Doctype2FixedWatermarkConvertor（三阶段链式转换）

阶段链：源格式→JSON（CL/Aspose）→ JSON→PDF（Canvas/Aspose）→ PDF→FIXED_WATERMARK（Java）

| 失败阶段 | 错误码 | 日志关键字 |
|----------|--------|-----------|
| 阶段1/2 子转换器失败 | 透传子转换器错误 | `[Doctype2FixedWatermarkConvertor]` |
| Canvas PDF 失败 | `UNKNOWN`(520) | `[Doctype2FixedWatermarkConvertor]` |
| 阶段3 Java 加水印失败 | 透传 PDFWatermarkConvertor 错误 | `[PDFWatermarkConvertor]` |

> 缓存模式（有 uniqueId）：保留 content.pdf + 输出 fixedWatermark.pdf
> 非缓存模式：直接覆盖 content.pdf

#### AsposeConvertor

日志标识：`[AConvertor]`（注意不是 AsposeConvertor）

| 场景 | 错误码 | 日志关键字 |
|------|--------|-----------|
| 不支持的源格式 | `CONVERT_NOT_SUPPORTED`(501) | `[AConvertor]` |
| Word IncorrectPasswordException | `INVALID_PASSWORD`(491) | Java 异常名 |
| Sheet CellsException code=8 | `INVALID_PASSWORD`(491) | Java 异常名 |
| 其他 Java 异常 | `UNKNOWN`(520) | `[AConvertor] Error` |

### Sheet→PDF（Sheet2PdfConvertor）

日志标识：`[Sheet2PdfConvertor]`

| 引擎 | 子转换器 | 日志前缀 |
|------|---------|---------|
| CANVAS | → MODEL_OPERATION(SaveAs) | `[ModelServiceWorker]` |
| ASPOSE（默认） | AsposeConvertor | `[AConvertor]` |

| 场景 | 错误码 |
|------|--------|
| 总 catch | `CONVERSION_UNKNOWN`(905) |
| Aspose 加密检测 | `INVALID_PASSWORD`(491) |

> 大小限制：51200 KB（50MB）

### PPT→PDF（Slide2PdfConvertor / JavaSAConvertor）

日志标识：`[JavaSAConvertor]` / `[Slide2PdfConvertor]`

JavaSAConvertor 启动独立 Java 进程（`java -cp ... com.filez.zdocs.conversion.pres.Converter`）：

| 场景 | 错误码 | 日志关键字 |
|------|--------|-----------|
| Redis 锁等待超时（默认 120s） | `CONVERT_STANDALONE_BUSY`(1011) | `java standalone convertor is busy` |
| Java 进程超时被 SIGKILL | `CL_TIMEOUT`(521) | `It's time to cancel task` |
| Java 进程 exit code = 1 | `JAVA_STANDALONE_CONVERT_ERR`(1012) | `[JavaSAConvertor] ExitCode` |
| Java 进程 exit code = 2 | `INVALID_PASSWORD`(491) | 加密 PPT |
| 其他非零 exit code | `UNKNOWN`(520) 或 exitCode+300 | `ExitCode` |

> 大小限制：102400 KB（100MB，配置 `pres2pdfLimit`）
> JavaSA 配置：`maxParallels=2`、`Xmx=3072m`、`maxWaitTimeInSecond=120`

Pres2FixedWatermarkConvertor（PPT 带水印）：PPT→PDF→PDFWatermarkConvertor（Java 加水印）

### 图片→PDF（Img2PdfConvertor）

日志标识：`[Img2PdfConvertor]`

| 场景 | 错误码 | 日志关键字 |
|------|--------|-----------|
| 图片文件损坏 | `ImgToPdfImageCorrupted`(470) | Java warning |
| 图片尺寸超限 | `ImgToPdfImageDimensionExceeded`(471) | Java warning |
| Java 转换失败 | `UNKNOWN`(520) | `[CommonJavaConvertor]` |

> 这两个错误码在 OpenAPI 异步任务失败回调中使用，不在入口同步返回。

### PDF 水印（PDFWatermarkConvertor）

日志标识：`[PDFWatermarkConvertor]`

| 场景 | 错误码 | 日志关键字 |
|------|--------|-----------|
| 水印图片下载失败/为空 | `UNKNOWN`(520) | `pic is empty` / `download ... error` |
| 水印图片过大 | `FILE_TOO_LARGE`(413) | `maxImgFileSize` |
| Java 转换失败 | `UNKNOWN`(520) | `[CommonJavaConvertor] Error` |

### CL/OOXML 二进制转换（CLConvertor）

日志标识：`[CLConvertor]` 或 `[ClConvertor]`（大小写混用）

| 场景 | 错误码 | 日志关键字 |
|------|--------|-----------|
| 超时（默认 5min） | `CL_TIMEOUT`(521) | `It's time to cancel task ... since time is over` |
| 子进程异常退出（exitCode 111-238） | exitCode + 300 | `ExitCode (code=X;errCode:Z;id=...)` |
| exitCode=412 | 需 batchConvert（二次转换） | `ExitCode` |
| 资源图片上传失败 | `UNKNOWN`(520) | `resourceUpload error` |
| 内容限制触发 | `CONTENT_UNSUPPORTED`(906) | `feature.log` 中的 warning |

> 超时配置：`task.timeout` || `MaxConvertTime`(300000ms)，提前 100ms 触发 cancel
> 子类：DOC2JSONConvertor、DOCX2JSONConvertor、XLSX2JSONConvertor、PPTX2HTMLConvertor

### OFD 相关

| 转换 | 转换器 | 页数限制 |
|------|--------|---------|
| OFD→PDF | OFD2PDFConvertor（Java plugin） | `ofd.max-pages=100` |
| PDF→OFD | PDF2OFDConvertor | — |
| 文档→OFD+水印 | Doctype2OFDWithWatermarkConvertor | — |

---

## ModelOp 两步路径专项排查

**触发条件**：`/content/update` 或 `/content/query` 接口，word/sheet 文档，ops 不在纯查询类列表中。

### 流程概览

```
step1: CONVERT 任务（Word/Sheet → JSON draft, uploadToDb=true）
  → LocalConvertServiceImp → CLConvertor（fork OOXML 二进制）
  → 完成 → [HttpModelOpServiceImpl] "get draft success"

step2: MODEL_OP 任务（加载 draft → 执行 ops → 输出结果）
  → ModelServiceWorker → ModelManager.processCanvasActionSaveAs
  → 子进程（大文件）或 线程（小文件 ≤ threadLimitSize）
  → CanvasModelService.wordSaveAsPdf / excelSaveAsPdf
  → 完成 → 回调调用方
```

### 各步骤失败表现及日志

| 失败点 | 日志前缀 | 关键日志内容 | 结果 |
|--------|---------|------------|------|
| step1 源文件下载失败 | `[LocalConvertServiceImp]` | `Download of task with error` | `CONVERT_DOWNLOAD_ERROR`(1001) |
| step1 文件过大/MIME不符/密码错误 | `[LocalConvertServiceImp]` | `Failed the validation <code>` | 对应 ConvertErrCode |
| step1 OOXML 转换失败/超时 | `[CLConvertor]` | `ExitCode (code=X;errCode:Z;id=...)` | 对应 ConvertErrCode |
| step1 CLConvertor 超时 cancel | `[CLConvertor]` | `It's time to cancel task ... since time is over` | `CL_TIMEOUT`(521) |
| step1 资源图片上传失败 | `[CLConvertor]` | `resourceUpload error` | `UNKNOWN`(520) |
| step1 转换结果上传失败 | `[LocalConvertServiceImp]` | 上传后无 storageRef | `CONVERT_UPLOAD_ERROR`(1002) |
| step2 ModelOp 队列满 | `[HttpModelOpServiceImpl]` | `model op busy` | 任务 FAIL 回调 |
| step2 ModelOp 不支持当前 ops | `[HttpModelOpServiceImpl]` | `model op fail` | 任务 FAIL 回调 |
| step2 ModelOp 执行异常 | `[OpenModelWorker]` | `model op with error <msg>` | `ModelOpCode.UNSUPPORT` |
| step2 ModelOp 超时 | `[ModelServiceWorker]` | `task is running...` | `ModelOpCode.TIMEOUT` |
| step2 Canvas 子进程超时被 kill | `[ModelManager]` | `Worker [pid] is killed because of timeout!` | processResult: false |
| step2 Canvas 子进程异常退出 | `[ModelManager]` | `Error is returned by Worker [pid]` | processResult: false |
| step2 Canvas 子进程 kill 失败 | `[ModelManager]` | `Worker [pid] is failed to be killed` | 进程可能残留 |
| step2 Canvas 渲染失败（word） | `[CanvasModelService]` | `Print writer To PDF Fail: <error>` | rethrow → UNSUPPORT |
| step2 Canvas 渲染失败（sheet） | `[CanvasModelService]` | `Print Excel To PDF Fail: <error>` | throw → UNSUPPORT |
| step2 CanvasProcess 无 docModel | `[CanvasProcess]` | `no docModel` | processResult: false |
| step2 draft 加载失败 | `[DraftStorageService]` | `Could not get draft` / `Could not find draft content` | throw → processAction catch |
| step2 draft apply messages 失败 | `[ModelManager]` | `APPLYMESSAGE_DOCTYPE_NOT_SUPPORTED` | throw |
| step2 draft 资源下载失败 | `[SaveAsHandler]` | `failed to copy ... resource to path` | 仅 warn，不中断 |
| step2 processAction 通用错误 | `[ModelManager]` | `processAction error` / `Failed to process canvas action` | processResult: false |
| step2 export 任务添加失败 | `[HttpModelOpServiceImpl]` | `export doc busy` | FAIL 回调 |
| step2 清理临时数据失败 | `[HttpModelOpServiceImpl]` | `clearTempData fail in ...` | 仅 error 日志，不影响回调 |
| step2 线程超时 | `[ThreadJob]` | `Faile to post message!` | `CONVERSION_TIMEOUT` |

### ModelOpCode 含义

| 值 | 字符串 | 含义 |
|----|--------|------|
| `NO_ERR` | `modelOpNoErr` | 成功 |
| `UNSUPPORT` | `modelOpUnSupport` | ops 不支持或执行失败 |
| `UNKNOWN` | `modelOpUnKnown` | 未知异常 |
| `QUEUE_BUSY` | `queueBusy` | 队列忙（定义但实际未使用） |
| `TransformDraftErr` | `transformDraftErr` | draft 转换失败 |
| `FILE_TOO_LARGE` | `fileTooLarge` | 文件过大 |
| `TIMEOUT` | `timeOut` | 超时被中断 |

### ModelOp 超时配置

| 配置项 | 默认值 | 用途 |
|--------|--------|------|
| `docscommon.task.config.modelOp.timeout` | 300000ms (5min) | ModelOp 任务/Canvas 子进程超时 |
| `docscommon.task.config.modelOp.ttl` | 360000ms (6min) | 未消费任务 TTL |
| `docscommon.task.config.modelOp.retry` | 0 | 不重试 |
| `taskserver.modelOp.threadLimitSize` | 1024 KB | 线程模式文件大小阈值 |
| `taskserver.modelOp.canvasInThread` | false | Canvas 是否走线程 |
| ThreadJob 默认 timeout | 300000ms | 可被 jobData.timeout 覆盖 |

> **注意**：整个 ModelOp 流程涉及多个异步任务，同一个 taskId（docId）会在日志中出现多次，需按时间顺序串联 step1/step2 的日志。step2 结果通过 Redis 缓存（TTL=5min）传回 DocsServer。

---

## 源文件下载专项排查

### 下载路径

| 来源 | 下载方式 | 超时 | 日志前缀 |
|------|---------|------|---------|
| fileUrl（OpenAPI） | RemoteContentImpl → axios GET | **10s** | `[RemoteContentImpl]` |
| docId/repoId | ContentService 本地存储拷贝 | — | `[ContentService]` |
| merge fileList | 循环下载各 downloadUrl | 10s/项 | `[RemoteContentImpl]` |

### 常见错误场景

| 场景 | 错误码 | 日志关键字 |
|------|--------|-----------|
| fileUrl DNS 解析失败 | `CONVERT_DOWNLOAD_ERROR`(1001) | `ENOTFOUND` |
| fileUrl 连接超时（10s） | `CONVERT_DOWNLOAD_ERROR`(1001) | `timeout` / `ETIMEDOUT` |
| fileUrl SSL 证书错误 | `CONVERT_DOWNLOAD_ERROR`(1001) | `CERT_` / `SSL` |
| fileUrl HTTP 非 200 | `CONVERT_DOWNLOAD_ERROR`(1001) | `download file error` |
| 下载文件 0 字节 | `CONVERT_DOWNLOAD_ERROR`(1001) | `0 bytes` / `size=...B is invalid` |
| OOXML 文件过小（< 1KB） | `DOWNLOAD_FILE_TOO_SMALL`(1014) | `is too small` |
| 远程文件解密失败 | `CONVERT_DOWNLOAD_ORIGIN_FILE_DECRYPT_ERROR`(1004) | `decrypt_fail` |

> **无重试机制**：源文件下载失败后直接返回错误，不重试。
> **临时目录**：`/tmp/zdocs/DOCS_CONVERT_*`，由 HouseKeepingService 定期清理。

---

## 文件校验专项排查

### 文件大小限制（单位 KB）

| 转换场景 | 限制 | 配置来源 |
|---------|------|---------|
| Word → PDF（OpenAPI） | 51,200 (50MB) | ConvertorConfig 固定 |
| Sheet → PDF | 51,200 (50MB) | ConvertorConfig 固定 |
| PPT/PPTX → PDF | 102,400 (100MB) | `pres2pdfLimit` |
| Word → JSON (draft) | 307,200 (300MB) | `maxfilesize.text` |
| Sheet → JSON | 307,200 (300MB) | `maxfilesize.sheet` |
| PPT → JSON | 102,400 (100MB) | `maxfilesize.pres` |
| PDF → * | 204,800 (200MB) | `maxfilesize.pdf` |
| 水印图片 | 51,200 (50MB) | `maxfilesize.img` |
| CSV（伪装 xlsx） | 51,200 (50MB) | `text_csv` |
| OOXML 最小合法大小 | 1 KB | `minCorrectFileSize` |
| merge 总大小 | 409,600 (400MB) | `source_total_size_limit` |

### MIME 类型校验

| 检测结果 | 错误码 | 行为 |
|---------|--------|------|
| 类型不匹配且无法修正 | `FILE_INVALID_MIMETYPE`(415) | 写 result.json 带 `correctSourceMIMEType` |
| 同 ApplicationGroup 可修正（如 doc↔docx） | 继续转换 | 日志 `correct mime type should be` |
| CSV 伪装 xlsx | 尝试按 CSV 加载 | 成功则改 format=CSV |
| Word/Excel XML、Flat OPC、MSO 格式 | 修正 format | 不报错 |

> MIME 检测通过 Java `FileTypeUtil.isCorrectFileType` 实现

### 加密文档检测

| 类型 | 检测方法 | 有密码时 | 无密码时 |
|------|---------|---------|---------|
| OOXML (docx/xlsx/pptx) | `isPwdProtectedOOXML_ls19` → Java `OOXMLPwdDetector` | 尝试 `decryptOOXML_ls19` | `INVALID_PASSWORD`(491) |
| ODF (odt/ods/odp) | `isPwdProtectedODF_ls19` | — | `UNSUPPORTED_ENCRYPTION`(492) |
| Aspose Word 加载 | Java `IncorrectPasswordException` | — | `INVALID_PASSWORD`(491) |
| Aspose Sheet 加载 | Java `CellsException` code=8 | — | `INVALID_PASSWORD`(491) |

### 内容限制检测阈值（来自 Java `conversion-config.json`）

| 类型 | 限制项 | 默认值 |
|------|--------|--------|
| Word | 最大页数 | 1,000 |
| Word | 最大纯文本大小 | 2,048 KB |
| Sheet | 最大行数 | 1,048,576 |
| Sheet | 最大列数 | 16,384 |
| Sheet | 最大单元格数 | 6,000,000 |
| Sheet | 最大公式单元格数 | 6,000,000 |
| PPT | 最大幻灯片数 | 300 |
| PPT | 最大图形对象数 | 3,000 |
| OFD | 最大页数 | 100 |

> 内容限制在 CL/Java 转换阶段检测，通过 `feature.log` 中的 warning 传递给 Node 层。

---

## 回调机制专项

### 回调消息体格式

**成功回调**：

```json
{
  "taskId": "uuid-string",
  "code": "TaskSuccessNotify",
  "detail": {
    "taskStatus": "SUCCESS",
    "startTime": 1234567890,
    "endTime": 1234567999,
    "defaultDownloadPath": "/context/publicapi/v1/download",
    "contentId": "mongo-content-id",
    "filename": "result.pdf",
    "msg": "Task success notification"
  }
}
```

**失败回调**：

```json
{
  "taskId": "uuid-string",
  "code": "TaskFailNotify",
  "detail": {
    "taskStatus": "FAIL",
    "startTime": 1234567890,
    "endTime": 1234567999,
    "msg": "model op error"
  }
}
```

> `code` 字段是枚举名称字符串（如 `"TaskSuccessNotify"`），不是数字。
> Convert 路径使用 `ConvertSuccessNotify`/`ConvertFailNotify`，ModelOp 路径使用 `TaskSuccessNotify`/`TaskFailNotify`。

### 回调错误处理

| 场景 | 处理方式 | 日志关键字 |
|------|---------|-----------|
| callbackUrl 为空 | 仅 error 日志，不发 HTTP | `failed to notify with wrong taskCtx` |
| 网络不可达/DNS 失败 | catch，仅 debug 日志 | `[AbstractOpenService] task notify with error` |
| HTTP 非 200 响应 | info 日志 | `notify res fail with ${status} ${statusText}` |
| 超时（5s） | axios timeout，同上 | `ETIMEDOUT` |

> **关键：回调无重试机制。** 回调失败后不会再次尝试发送。
> 回调超时硬编码 5s，不可配置。
> 认证方式：`zOffice-auth`（S2S MD5）/ header / cookie。

### 未收到回调的排查思路

1. 确认任务是否已完成：搜索 taskId 的日志，看是否有转换完成/失败的记录
2. 确认回调 URL 是否正确：检查 `notify res fail` 或 `task notify with error` 日志
3. 确认网络连通性：回调从 TaskServer 所在节点发出，检查该节点到 callback 地址的网络
4. 确认 callbackUrl 未被白名单拦截：看参数校验阶段是否已返回 `CallbackUrlNotAllowed`

---

## 下载机制专项

### 下载流程

```
GET /publicapi/v1/download?taskId=xxx&contentId=yyy
  → preCheck_ls19：taskId 非空
  → OpenServiceAuth_ls19：HMAC 鉴权
  → downloadResult_ls19：
      a) contentId 非空检查
      b) isAllowed_ls19：Redis TaskBelongTo 校验 taskId 有效性 + userId 归属
      c) getContentImpl_ls19：从存储读取文件流
      d) 设置 Content-Disposition + Content-Type 头返回
```

### 下载错误场景

| 场景 | 错误码 | 日志关键字 |
|------|--------|-----------|
| taskId 为空 | `InvalidTaskId`(423) | `taskId is null` |
| contentId 为空 | `ContentIdIsNull`(426) | — |
| taskId 在 Redis 中不存在（已过期） | `InvalidTaskId`(423) | — |
| userId 与任务归属不匹配 | `AccessOtherRepoIsNotAllowed`(424) | — |
| contentId 对应的 contentEntry 不存在 | `ContentIdError`(425) | `[ContentService] getContent Could not find content` |
| 存储信息缺失 | `ContentIdError`(425) | `Could not find storage info` |
| 不支持的存储类型（如 S3） | `DownloadErr`(502) | `Not support type` |
| 文件流读取异常 | `DownloadErr`(502) | `[downloadResult] get file stream error` |

> **下载 TTL**：`downloadExpireInMinute` 默认 60 分钟。超过此时间 Redis key 消失，queryTaskStatus 返回 InvalidTaskId。
> **TaskBelongTo TTL** = `downloadExpireInMinute × 60` 秒，控制下载权限有效期。

---

## 任务队列管理

### 队列实现

- 底层：**Redis + Bull**（`QueueProviderType_ls19.REDIS_ls19`）
- 多节点：各 TaskServer 实例竞争消费同一 Bull 队列
- 并发度：`taskserver.convert.maxParallels`（默认 16）

### 限流机制（OpenAPI 专用）

OpenAPI 使用独立的 Redis ZSET 限流（非 Bull waiting count）：

1. `rateLimitCheck_ls19` 检查活跃 OpenAPI 任务数
2. `maxActiveTask = taskServerSum × capabilityIndex × convertMaxParallels`
3. 超限 → `TaskQueueCongestion`(505)
4. CPU License 超限 → `TaskQueueCongestion`(505)

| 限流因素 | 配置/默认值 | 说明 |
|---------|------------|------|
| capabilityIndex | 0.5 | OpenAPI 容量系数 |
| convertMaxParallels | 16 | convert Worker 并发 |
| taskServerSum | 取决于部署 | 活跃 TaskServer 节点数 |
| CPU License | `ConcurrentCPULicenseUtils` | License 级并发控制 |

### 任务超时与清理

| 机制 | 配置/行为 |
|------|-----------|
| Bull job timeout | `docscommon.task.config.default.timeout` = 180000ms |
| Bull job 重试 | `docscommon.task.config.default.retry` = 3 |
| 队列 TTL 检查 | waiting 超时 → EXPIRED |
| Dead 检查 | ACTIVE 超过 `timeout × 3` → DEAD |
| OpenAPI 过期清理 | `removeExpiredTask_ls19(..., now-1h)` 在 rate limit 失败时触发 |
| Redis 队列可见性超时 | `visibilityTimeout` = 300s |

### Bull 队列任务状态

| Bull 状态 | OpenAPI 语义 |
|-----------|-------------|
| waiting | IN_QUEUE |
| active | 处理中（Redis 为 TaskHandingNotify） |
| completed | 已完成（Redis 为 TaskSuccessNotify） |
| failed | 失败（Redis 为 TaskFailNotify） |
| delayed | 延迟任务（重试场景） |

---

## Java 调用层专项

### 两种 Java 调用方式

| 方式 | 实现 | 典型场景 | 日志前缀 |
|------|------|---------|---------|
| 嵌入式 JVM（node-java bridge） | CommonJavaConvertor / AsposeConvertor | 水印、OOXML→JSON、格式转换 | `[CommonJavaConvertor]` `[AConvertor]` |
| 独立 Java 进程（spawn） | JavaSAConvertor | PPT→PDF | `[JavaSAConvertor]` |

### 嵌入式 JVM 错误

| 场景 | 错误码 | 表现 |
|------|--------|------|
| Java 异常 | `UNKNOWN`(520) | bridge 异常，无细分 |
| Java OOM | 表现为 bridge 异常 | 无专门 OOM 检测 |
| Java warning/错误码 | 解析 `featureID`/`conv_err_code` | 映射到 ConvertErrCode |

> JVM 配置：`-Xms1024m -Xmx2048m -Djava.awt.headless=true -Xrs`
> Java 日志通过 `ConversionLogger.printExternalLog` 写入 `java*.log`，与 Node 日志分开。

### 独立 Java 进程错误（JavaSAConvertor）

| 场景 | exit code | 错误码 |
|------|-----------|--------|
| 转换成功 | 0 | `NOERR`(0) |
| 转换失败 | 1 | `JAVA_STANDALONE_CONVERT_ERR`(1012) |
| 加密文件 | 2 | `INVALID_PASSWORD`(491) |
| 超时被 kill | — | `CL_TIMEOUT`(521) |
| 其他 exit code (111-238) | 111-238 | exitCode + 300 |
| Redis 锁等待超时 | — | `CONVERT_STANDALONE_BUSY`(1011) |

> JavaSA 配置：`maxParallels=2`（Redis 锁控制并行）、`Xmx=3072m`、`maxWaitTimeInSecond=120`
> stdout/stderr 通过 `[JavaSAConvertor] stdout|stderr (id=...)` 日志记录。

---

## 各接口路由差异

| 接口 | 中间件链（关键差异） | 额外校验 |
|------|---------------------|---------|
| `/convert` | callback + generalParamCheck + checkParamTargetFilename + handleConvert | 最完整的参数校验链 |
| `/content/update` | modelOpHandler | ops 校验、水印 actId 校验，不走 generalParamCheck |
| `/content/query` | 同上 | 查询类 ops，简化校验 |
| `/protect` | callback + generalParamCheck + protectFixParam | password AES 解密校验 |
| `/merge` | callback + checkParamFileList + mergeFixParam | fileList 校验（不走 generalParamCheck，filename 推迟校验） |
| `/split` | callback + generalParamCheck + splitFixParam | type + keyword 校验 |
| `/image/transform` | callback + generalParamCheck + imgFixParam | toPicOptions + imgToPdfOptions 校验 |
| `/queryTaskStatus` | preCheck + auth | 仅 taskId 校验 |
| `/convert/download` `/download` | preCheck + auth + downloadResult | taskId + contentId 校验 |

> Demo 路由（`/api/local/:docid/demoOpenapi/*`）：跳过 HMAC 鉴权、白名单校验，仅允许 `Repo.Local`。

---

## 超时配置汇总

| 组件 | 配置项 | 默认值 |
|------|--------|--------|
| Bull 任务超时 | `docscommon.task.config.default.timeout` | 180,000ms (3min) |
| Bull 任务重试 | `docscommon.task.config.default.retry` | 3 |
| ExportDoc 任务超时 | `docscommon.task.config.exportDoc.timeout` | 300,000ms (5min) |
| ModelOp 任务超时 | `docscommon.task.config.modelOp.timeout` | 300,000ms (5min) |
| ModelOp 任务 TTL | `docscommon.task.config.modelOp.ttl` | 360,000ms (6min) |
| 长时 Model 任务超时 | `docscommon.task.config.longTimeModel.timeout` | 600,000ms (10min) |
| CLConvertor 超时 | `task.timeout` \|\| `MaxConvertTime` | 300,000ms (5min) |
| JavaSAConvertor 超时 | 同 task.timeout | 300,000ms (5min) |
| JavaSA 锁等待超时 | `maxWaitTimeInSecond` | 120s |
| Java 内嵌转换超时 | `taskserver.convert.local.java.taskTimeout` | 300,000ms (5min) |
| Puppeteer 页面超时 | task.timeout | 300,000ms (5min) |
| Puppeteer launch 超时 | 硬编码 | 10,000ms (10s) |
| Redis 队列可见性超时 | `docscommon.task.visibilityTimeout` | 300s (5min) |
| 源文件下载超时（OpenAPI） | RemoteContentImpl 硬编码 | 10,000ms (10s) |
| 回调通知超时 | AbstractOpenService 硬编码 | 5,000ms (5s) |
| 下载/状态 Redis TTL | `docsserver.services.open.downloadExpireInMinute` | 60min |
| ModelOp 结果 Redis TTL | 硬编码 | 300s (5min) |
| ThreadJob 超时 | `jobData.timeout` | 300,000ms (5min) |

---

## 常见复合场景诊断

### 场景1：任务提交成功但一直 IN_QUEUE

```
排查步骤：
1. rg '<taskId>' 所有节点日志 → 确认是否被 TaskServer 消费
2. 未消费：检查 TaskServer 是否存活、队列是否堆积
3. 已消费但无后续日志：检查 TaskServer 是否 hang/OOM
4. rg 'maxActiveTask' → 检查是否因限流导致队列排队
5. 确认 Bull 队列状态：可能 waiting 太多，或 active 被 dead check
```

### 场景2：任务 FAIL 但无明确错误信息

```
排查步骤：
1. 搜索 taskId 的完整日志时间线
2. 查看失败回调中的 detail.msg（通常是 parseDocErrCode 转换后的文本）
3. 搜索 [convertReqHandler] 或 [LocalConvertServiceImp] 的 error 日志
4. 搜索 java*.log 中对应时间段的 ERROR/Exception
5. 检查是否是 ConvertErrCode.UNKNOWN(520)（兜底错误码，需看 stack trace）
```

### 场景3：Word→PDF 转换结果为空白/内容缺失

```
排查步骤：
1. 确认使用的转换引擎（Text2PdfConvertor 日志中有 watermarkType/engine 信息）
2. Canvas 引擎：检查 CL 转换阶段是否有 CONTENT_UNSUPPORTED(906) 警告
3. 检查 feature.log 中的 UNSUPPORT_FEATURE_WORD_* 警告
4. Aspose 引擎：检查 java*.log 中的 warning
5. 检查文件是否有特殊格式（macro/VBA/ActiveX/OLE 等不支持特性）
```

### 场景4：回调地址收不到通知

```
排查步骤：
1. rg '[AbstractOpenService].*notify' → 确认是否尝试发送了回调
2. rg 'failed to notify with wrong taskCtx' → taskCtx 异常导致未发送
3. rg 'notify res fail' → 回调发送了但对方返回非 200
4. rg 'task notify with error' → 回调发送失败（网络/DNS/超时）
5. 确认回调 URL 的可达性（从服务器节点验证）
6. 注意：回调无重试机制，失败即丢失
```

### 场景5：下载接口报 InvalidTaskId

```
排查步骤：
1. 确认 taskId 是否正确（来自提交时的响应或回调中的 taskId）
2. 检查时间差：默认 TTL 60 分钟，超时后 Redis key 消失
3. 多节点：确认是否跨节点请求（taskId 写入公共 Redis，理论不受节点影响）
4. 检查 Redis 连接是否正常
```

### 场景6：ServerBusy 频繁出现

```
排查步骤：
1. rg 'maxActiveTask' → 查看当前限流阈值
2. rg 'concurrent exceeds license limit' → CPU License 超限
3. rg 'get queue lock fail' → Redis 锁获取失败
4. 检查 TaskServer 节点数是否正常（节点下线会降低 maxActiveTask）
5. 检查是否有大量慢任务占用队列（大文件、复杂文档）
6. 检查 Bull 队列中 active/waiting 任务数
```

### 场景7：Java 转换层错误（java*.log 有异常）

```
排查步骤：
1. 搜索 java*.log 中的 ERROR/Exception/OutOfMemory 等关键字
2. 提取异常时间戳，与 combined*.log 中对应 taskId 的时间线对照
3. Java OOM：检查 JVM 配置（嵌入式默认 Xmx=2048m，JavaSA 默认 Xmx=3072m）
4. Aspose 异常：通常映射为 UNKNOWN(520)，需看 Java 异常类名
5. 水印 Java 异常：检查水印图片是否正常（下载/格式/大小）
6. PPT→PDF（JavaSA）：检查 exit code 和 stdout/stderr 输出
```

### 场景8：MIME 类型校验失败

```
排查步骤：
1. rg 'Invalid File mime type' → 确认检测到的实际 MIME 类型
2. rg 'correct mime type should be' → 是否可修正
3. 检查文件是否被改名（如 .xlsx 实际是 CSV，.doc 实际是 RTF/HTML）
4. 特殊格式：Word XML / Excel XML / Flat OPC / MSO 格式会自动修正
5. 校验使用 Java FileTypeUtil（Magic Bytes 检测），不依赖扩展名
```

---

## 输出报告结构

1. **错误阶段**：⓪全局中间件 / ①鉴权许可 / ②参数校验 / ③任务入队 / ④源文件下载 / ⑤文件校验 / ⑥转换执行 / ⑦回调通知 / ⑧下载
2. **错误码**：ResCode 名称 + 值 + 含义 / ConvertErrCode 值 + 含义 / ModelOpCode（如涉及）
3. **关键日志**：带时间戳的原始日志片段（保留完整行），跨节点标注节点名
4. **转换引擎**：使用的具体转换器（如 WebPrintConvertor / AsposeConvertor / JavaSAConvertor 等）
5. **根因**：结合链路知识分析具体原因，区分直接原因与深层原因
6. **修复建议**：参数修正 / 配置调整 / 文件问题 / 基础设施问题 / 重试策略

---

## 注意事项

- 一个请求可能跨节点处理（DocsServer 入队 → 其他节点 TaskServer 执行），搜索 taskId 时**必须覆盖所有节点**
- `[convertReqHandler]` 中异常的 `e.message` 直接作为 code 返回，是 ResCode 的数值字符串（如 `"513"`），会被 `getResCode` 转为枚举名
- 入口日志（DocsServer）和转换日志（TaskServer）时间上存在异步间隔，时间线构建时注意区分
- 水印相关错误同时可能有 ResCode（入口校验失败）和 ConvertErrCode（转换失败）两个层面
- ModelOp 路径的 handler 层 preHandle 校验（如 SaveAsHandler、ApplyPicWatermarkHandler）失败时通过异步 callback 返回，**非同步 HTTP 响应**
- Demo 路由跳过 HMAC 鉴权和白名单校验，排查时注意区分路由类型
- SaaS Quota 校验失败 HTTP 仍是 200，错误在 body.code 中
- 鉴权失败 HTTP 401 的 body 格式与业务接口不同（message + errorMsg vs formatRes）
- 507-512 是通知码（用于 callback/Redis），不是入口同步错误码
- `CONTENT_UNSUPPORTED`(906) 是警告而非致命错误，转换可能仍成功
- Bull 任务默认重试 3 次（`docscommon.task.config.default.retry=3`），但 ModelOp 任务不重试（`retry=0`）
- Java 层错误需同时查看 `combined*.log`（Node 侧日志）和 `java*.log`（Java 侧日志）

---

## 脚本辅助

```
docs/skills/scripts/analyze_openapi_failure.py
```

Python 3.6+，无额外依赖。分析日志时请直接通过 run_script 工具调用此脚本。

### 执行方式

```bash
# 按 taskId 分析（可指定多个）
python3 docs/skills/scripts/analyze_openapi_failure.py --logDir <LOG_DIR> --taskId <taskId1> <taskId2>

# 按时间段分析所有 OpenAPI 失败任务
python3 docs/skills/scripts/analyze_openapi_failure.py --logDir <LOG_DIR> --timeFrom "2026-05-22 14:00:00" --timeTo "2026-05-22 15:00:00"

# 同时输出成功任务
python3 docs/skills/scripts/analyze_openapi_failure.py --logDir <LOG_DIR> --timeFrom "2026-05-22 14:00:00" --timeTo "2026-05-22 15:00:00" --all
```

### 脚本功能

- **日志目录自动发现**：递归扫描指定路径，自动识别 `NewDocServer_*`、`TaskServer_*` 等日志目录
- **NewDocServer 日志扫描**：提取鉴权失败、License 校验、队列限流、回调通知、下载错误等入口层事件
- **TaskServer 日志扫描**：提取任务接收、源文件下载、文件校验、转换执行（CL/Aspose/JavaSA/Canvas）、MIME/加密检测等事件
- **Java 日志扫描**：提取 `java*.log` 中的 ERROR/Exception/OutOfMemory 等 Java 层错误
- **时间段自动发现 taskId**：无 taskId 时按时间段自动扫描 OpenAPI 相关日志关键字提取 taskId
- **失败原因分类**：自动分类为 OOM、超时、下载失败、MIME 不匹配、加密文档、服务繁忙等
- **完整时间线**：按时间排序输出每个任务的完整事件时间线
- **汇总统计**：输出失败阶段分布和失败原因分布
