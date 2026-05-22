---
name: lse-openapi-error-log
description: 分析 luoshu-server OpenAPI 错误日志，覆盖请求入口（OpenController）→ 任务队列 → 转换执行（Text2PdfConvertor 等）→ 下载全链路。当用户提到 OpenAPI 报错、转换失败、word转pdf错误、任务超时、ResCode、ConvertErrCode、回调失败、下载失败时触发。
disable-model-invocation: true
---

# lse-openapi-error-log：OpenAPI 错误日志分析

## 请求全链路

```
① 参数校验/鉴权
   HTTP 请求 → License检查 → Quota检查 → 参数合法性校验（filename/fileUrl/callback/水印参数等）
   → 校验失败：立即返回 ResCode（4xx段），接口同步报错

② 任务入队
   → addTask_ls19 → 写入任务队列
   → 入队成功：返回 { taskId, status: "IN_QUEUE" }
   → 入队失败：返回 ServerBusy / 抛出异常

③ 转换执行（TaskServer 异步）

   【直接转换路径：/convert 接口，带水印场景】
   → TaskServer 取出任务 → Text2PdfConvertor（word→PDF 分发入口）
       ├── WebPrintConvertor       （PUPPETEER 引擎）
       ├── CanvasConvertor         （CANVAS 引擎，非 API 任务）
       ├── Doctype2FixedWatermarkConvertor  （CANVAS + API_TASK）
       └── AsposeConvertor         （默认引擎）
   → 执行结果通过 callback URL 回调给调用方

   【ModelOp 两步路径：/content/ops 接口，word→PDF 不加水印场景】
   step1: CONVERT 任务 — Word → JSON（内部草稿格式，uploadToDb: true）
       → LocalConvertServiceImp（下载源文件 → 文件校验 → 执行转换 → 上传结果）
           → CLConvertor（fork 原生 OOXML 转换二进制，将 word/xlsx 转为 JSON draft）
       → 完成后触发 [HttpModelOpServiceImpl] "get draft success"
   step2: MODEL_OP 任务 — 对 JSON 执行 SaveAs 操作，直接产出 PDF 结果文件
       → ModelServiceWorker → ModelManager.processCanvasActionSaveAs
           → 从 DB 或本地目录加载 draft（JSON 模型）
           → 根据文件大小决定执行方式：
               ├── 子进程（大文件/默认）: fork CanvasProcess.js → CanvasModelService.wordSaveAsPdf
               └── 线程（小文件+canvasInThread=true）: ThreadJob → CanvasModelService.wordSaveAsPdf
           → CanvasModelService 用 SwPrintController（word）/ ScPrintController（sheet）渲染 PDF
       → 完成后回调调用方

④ 下载
   → 调用方收到成功回调后，用 taskId + contentId 调用下载接口
   → downloadResult 从存储取文件流返回
```

> `[openApiStatistics]` 是统计中间件本身出错的日志，**不代表请求失败**，不能用于阶段判断。

---

## 阶段定位：根据现象判断

| 现象 | 所在阶段 | 关键日志前缀 |
|------|---------|-------------|
| 接口立即返回非 0 code，无 taskId | ① 参数校验/鉴权 | 无固定前缀，看 ResCode 数值（400–499 段） |
| 接口返回 ServerBusy / 抛出异常，无 taskId | ② 任务入队 | `[convertReqHandler]` |
| 有 taskId，queryTaskStatus 返回 FAIL | ③ 转换执行 | `[Text2PdfConvertor]` |
| 有 taskId，收到 FAIL 回调后调下载接口报错 | ④ 下载 | `[downloadResult]` |
| ModelOp 接口（word→PDF 不加水印）失败 | ①③ | `[modelOpHandler]` `[HttpModelOpServiceImpl]` `[ModelServiceWorker]` `[OpenModelWorker]` |

---

## 日志搜索

日志文件说明：
- `combined-*.log`：所有级别日志（info/warn/error）
- `error-*.log`：仅 error 级别
- `java*.log`：Node 进程通过 node-java 调用 Java 库时的输出（如加水印、格式转换），与 Node 日志分开写入

日志路径默认为 `/opt/zdocs/luoshu_log/*/`，根据实际部署情况调整（下文以 `$LOG_DIR` 代替）。

```bash
LOG_DIR=/opt/zdocs/luoshu_log

# 按 taskId 串联全链路（必须搜所有节点、所有日志文件）
rg '<taskId>' $LOG_DIR/*/{combined,error,java}*.log* | sort

# 转换执行错误
rg '\[Text2PdfConvertor\]' $LOG_DIR/*/{combined,error}*.log*

# 任务入队失败
rg '\[convertReqHandler\]' $LOG_DIR/*/{combined,error}*.log*

# 下载错误
rg '\[downloadResult\]' $LOG_DIR/*/{combined,error}*.log*

# ModelOp 入口错误
rg '\[modelOpHandler\]' $LOG_DIR/*/{combined,error}*.log*

# ModelOp 两步流程协调（DocsServer 侧）
# step1 成功时会出现 "get draft success"；若缺失说明 step1 转换失败
rg '\[HttpModelOpServiceImpl\]' $LOG_DIR/*/{combined,error}*.log*

# ModelOp step1 (Word→JSON) 失败：LocalConvertServiceImp + CLConvertor
rg '\[LocalConvertServiceImp\]|\[CLConvertor\]' $LOG_DIR/*/{combined,error}*.log*

# ModelOp 执行错误（TaskServer 侧）
rg '\[ModelServiceWorker\]|\[OpenModelWorker\]' $LOG_DIR/*/{combined,error}*.log*

# Canvas 渲染错误（step2 子进程/线程）
rg '\[ModelManager\]|\[CanvasModelService\]' $LOG_DIR/*/{combined,error}*.log*

# node-java 调用 Java 库的输出（加水印、格式转换）
rg 'ERROR|Exception' $LOG_DIR/*/java*.log*
```

---

## ResCode 速查（OpenController 层，HTTP 同步返回）

> 数值从 400 起连续递增，以下标注关键值。

### 4xx — 客户端问题（不可重试，需修正参数/配置）

| ResCode 名称 | 含义 | 常见原因 |
|-------------|------|---------|
| `InvalidAuthHeader` (400) | Token 校验失败 | 签名错误、Token 格式不对 |
| `InvalidAuthTimestamp` (402) | 时间戳过期 | 超过 30s 偏差 |
| `LicenseNotFound` (407) | License 不存在 | 未配置 License |
| `InvalidLicense` (408) | License 无效 | License 内容错误 |
| `LicenseIsExpire` (409) | License 已过期 | 续费或更新 License |
| `FilenameIsNull` (410) | filename 为空 | 请求体缺 filename 字段 |
| `TargetFilenameIsNull` (411) | targetFilename 为空 | /convert 接口缺目标文件名 |
| `CallbackIsNull` (414) | callback 为空 | 忘传 callback 参数 |
| `SourceInfoIsNull` (416) | fileUrl/docId 均为空 | 来源信息缺失 |
| `ConflictSourceInfo` (415) | fileUrl 与 docId 同时传 | 二选一 |
| `FileUrlNotAllowed` (418) | fileUrl host 不在白名单 | 配置中未加该域名 |
| `CallbackUrlNotAllowed` (419) | callback host 不在白名单 | 配置中未加该域名 |
| `DocTypeNotSupport` (420) | 文件类型不支持 | 格式组合不支持转换 |
| `InvalidTaskId` (422) | taskId 无效或已过期 | 默认 1h 过期，重新发起任务 |
| `ContentIdError` (424) | contentId 对应文件不存在 | 任务未成功就调下载 |
| `ContentIdIsNull` (425) | 下载接口未传 contentId | 从回调 detail 中取 |
| `ParamTypeError` (431) | 参数类型错误 | 数字字段传了字符串 |
| `ParamOutOfRange` (432) | 参数超出范围 | 如 transparent 超出 [0,100] |
| `InvalidSecret` (447) | rand32 密钥未配或长度不等于 32 | 检查服务端配置 |
| `DecryptFail` (448) | AES-256-ECB 解密密码失败 | 密钥不匹配 |
| `WMPicTypeNotSupport` (453) | 水印图片格式不支持 | 只支持 jpg/jpeg/png/bmp |
| `WMPicIsNotSupport` (454) | 水印图片不支持（line1 为空） | tiledWatermark 缺 line1 |
| `AppIsExpired` | 应用已过期（SaaS） | 联系运营续期 |
| `ApplicationNotFound` | 应用不存在（SaaS） | repoId 未注册 |

### 5xx — 服务端问题（可重试）

| ResCode 名称 | 含义 | 处理建议 |
|-------------|------|---------|
| `UnknownErr` (500) | 未知错误 | 看 stack trace |
| `DownloadErr` (502) | 下载源文件失败 | 检查 fileUrl 可达性、网络 |
| `TaskTimeout` (503) | 任务执行超时 | 检查 TaskServer 负载 |
| `NotSupportConvertType` (504) | 转换类型不支持 | 格式组合在 TaskServer 层不支持 |
| `TaskQueueCongestion` (505) | 队列拥堵 | 稍后重试，关注队列积压 |
| `ServerBusy` (506) | 队列已满，任务未入队 | 同上 |
| `NotSupportTask` | 参数组合不支持（TaskServer 层） | 检查文件扩展名或 actId |

---

## ConvertErrCode 速查（TaskServer 转换层）

### 可重试

| Code | 值 | 含义 |
|------|----|------|
| `SOFFICE_BUSY` | 493 | soffice 实例全忙 |
| `SOFFICE_UNAVILABLE` | 496 | soffice 连接不可用 |
| `CL_TIMEOUT` | 521 | Canvas/CL 渲染超时 |
| `IO_EXCEPTION` | 522 | IO 异常 |
| `SINGLE_PAGE_OVERTIME` | 523 | 单页渲染超时 |
| `SOFFICE_RUNTIME_ERROR` | 495 | soffice 崩溃/超时 |

### 文件/内容问题（不可重试）

| Code | 值 | 含义 |
|------|----|------|
| `FILE_TOO_LARGE` | 413 | 文件过大 |
| `FILE_INVALID_MIMETYPE` | 415 | MIME 类型与扩展名不符 |
| `MIME_TYPE_MODIFIED` | 526 | 实际类型与扩展名不符 |
| `INVALID_PASSWORD` | 491 | 文档加密，需要密码 |
| `UNSUPPORTED_ENCRYPTION` | 492 | 不支持的加密方式 |
| `CORRUPTED_FILE` | 529 | 文件损坏 |
| `CONVERT_NOT_SUPPORTED` | 501 | 该格式转换不支持 |
| `CONTENT_LIMIT` (多值) | 530–539 | 内容超限：页数/字数/行列数/对象数 |
| `DOWNLOAD_FILE_TOO_SMALL` | — | 下载文件过小，疑似损坏 |

---

## word→PDF 专项（Text2PdfConvertor）

日志标识：`[Text2PdfConvertor] [<tid>]`

**引擎选择规则**（按配置 `taskserver.exportDoc.toPdf.text`）：

| 配置引擎 | 任务类型 | 实际使用的转换器 |
|---------|---------|---------------|
| PUPPETEER | 任意 | WebPrintConvertor |
| CANVAS | 非 API_TASK | CanvasConvertor |
| CANVAS | API_TASK | Doctype2FixedWatermarkConvertor |
| as1（默认） | 无 revisions | Doctype2FixedWatermarkConvertor |
| as1（默认） | 有 revisions | AsposeConvertor |

**水印与转换路径**：

| 水印参数 | 类型 | 执行路径 |
|---------|------|---------|
| `tiledWatermark`（无 picUrl） | 平铺文字水印 | Canvas 直接渲染，一步完成 |
| `tiledWatermark`（有 picUrl） | 平铺图片水印 | Canvas→PDF 后 Java 加水印 |
| `msTextWatermark` | 固定位置文字水印 | Canvas→PDF 后 Java 加水印 |
| `msPicWatermark` | 固定位置图片水印 | Canvas→PDF 后 Java 加水印 |

> `uniqueId` 场景（固定水印复用 PDF 缓存）走独立服务，**不经过** `Text2PdfConvertor`。

---

## ModelOp 两步路径专项排查

**触发条件**：`/content/ops` 接口，word/sheet 文档（DocumentType.TEXT/SHEET），ops 不在查询类列表中（非 UpdateBookmarkRef/QueryBookmarkRef 等）。

**各步骤失败表现及日志**：

| 失败点 | 日志前缀 | 关键日志内容 | 结果 |
|--------|---------|------------|------|
| step1 源文件下载失败 | `[LocalConvertServiceImp]` | `Download of task with error` | `CONVERT_DOWNLOAD_ERROR` |
| step1 文件过大/MIME不符/密码错误 | `[LocalConvertServiceImp]` | `Failed the validation <code>` | 对应 ConvertErrCode |
| step1 OOXML 二进制转换失败/超时 | `[CLConvertor]` | `ExitCode (code=X;errCode:Z;id=...)` | 对应 ConvertErrCode |
| step1 CLConvertor 超时 cancel | `[CLConvertor]` | `It's time to cancel task ... since time is over` | `CL_TIMEOUT` |
| step1 资源图片上传失败 | `[CLConvertor]` | `resourceUpload error` | `UNKNOWN` |
| step1 转换结果上传失败 | `[LocalConvertServiceImp]` | `Uploading result of task...` 后无 storageRef | `CONVERT_UPLOAD_ERROR` |
| step2 ModelOp 队列满，提交失败 | `[HttpModelOpServiceImpl]` | `model op busy` | 任务 FAIL 回调 |
| step2 ModelOp 不支持当前 ops | `[HttpModelOpServiceImpl]` | `model op fail` | 任务 FAIL 回调 |
| step2 ModelOp 执行异常 | `[OpenModelWorker]` | `model op with error <msg>` | `ModelOpCode.UNSUPPORT` |
| step2 ModelOp 超时 | `[ModelServiceWorker]` | `service will exit after...` | `ModelOpCode.TIMEOUT` |
| step2 Canvas 子进程超时被 kill | `[ModelManager]` | `Worker [pid] is killed because of timeout!` | 子进程 exit code != 0，processResult: false |
| step2 Canvas 子进程异常退出 | `[ModelManager]` | `Error is returned by Worker [pid]` | processResult: false |
| step2 Canvas 渲染失败（word） | `[CanvasModelService]` | `Print writer To PDF Fail: <error>` | 抛出异常，ModelOpCode.UNSUPPORT |
| step2 Canvas 渲染失败（sheet） | `[CanvasModelService]` | `Print Excel To PDF Fail: <error>` | 抛出异常，ModelOpCode.UNSUPPORT |
| step2 draft 加载/canvas 操作失败 | `[ModelManager]` | `Failed to process canvas action` | 返回 null，触发 model op busy 回调 |

**ModelOpCode 含义**：
- `NO_ERR`：成功
- `UNSUPPORT`：ops 不支持或执行失败
- `TIMEOUT`：ModelOp 任务超时被中断
- `UNKNOWN`：未知异常

**Canvas 子进程超时配置**：`docscommon.task.config.modelOp.timeout`（默认 300000ms = 5min），超时后 SIGKILL 子进程。

**注意**：整个 ModelOp 流程涉及多个异步任务，同一个 taskId（docId）会在日志中出现多次，需按时间顺序串联 step1/step2 的日志。

---

## 输出报告结构

1. **错误阶段**：① 参数校验 / ② 入队 / ③ 转换执行 / ④ 下载
2. **错误码**：ResCode 名称 + 含义 / ConvertErrCode 值 + 含义
3. **关键日志**：带时间戳的原始日志片段（保留完整行）
4. **根因**：结合链路知识分析具体原因
5. **修复建议**：参数修正 / 配置调整 / 重试策略

## 注意事项

- 一个请求可能跨节点处理（NewDocServer 入队 → 其他节点 TaskServer 执行），搜索 taskId 时**必须覆盖所有节点**
- `[convertReqHandler]` 中异常的 `e.message` 直接作为 code 返回，是 ResCode 的字符串名称
- 入口日志（NewDocServer）和转换日志（TaskServer）时间上存在异步间隔，时间线构建时注意区分
- 水印相关错误同时可能有 ResCode（入口校验失败）和 ConvertErrCode（转换失败）两个层面
