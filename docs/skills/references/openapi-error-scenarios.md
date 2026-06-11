# OpenAPI 报错场景速查

> 本文档归纳了 OpenAPI 接口调用过程中各类报错的典型场景、callback/响应特征和诊断要点。
> **快速定位原则**：先看 callback（或 queryTaskStatus）的 `code` + `detail.msg`，即可初步判断错误类型，再去日志中确认细节。

---

## 快速定位：通过 callback/queryTaskStatus 判断错误类型

### 第一步：看 code 字段

| `code` 值 | 含义 | 下一步 |
|-----------|------|--------|
| `TaskSuccessNotify` / `ConvertSuccessNotify` | 成功 | 检查 `detail.contentId` 是否可下载 |
| `TaskFailNotify` / `ConvertFailNotify` | 失败 | 看 `detail.msg` 判断具体原因 |
| `TaskHandingNotify` | 仍在处理中 | 等待或超时排查 |
| `InvalidTaskId` | taskId 不存在或已过期 | 检查是否超过 60min TTL |

### 第二步：看 detail.msg 快速分类

| msg 关键词 | 错误场景 | 对应场景编号 |
|-----------|---------|------------|
| `conversion timeout` | 转换超时 | S3 |
| `conversion unknown` | 转换未知错误（通用） | S1 |
| `conversion invalid password` | 加密文档 | S4 |
| `conversion download err` | 源文件下载失败 | S2 |
| `conversion fotmat err` | 文件格式/MIME错误 | S5 |
| `conversion soffice err` | Symphony 引擎错误 | S6 |
| `content toolarge` | 文件过大 | S7 |
| `model op busy` | ModelOp 队列满 | S8 |
| `model op fail` / `model op error` | ModelOp 执行失败 | S8 |
| `export doc busy` | 导出队列满 | S8 |
| JSON 字符串（含 `isSucceed`/`detail`） | TaskServer 原始错误详情 | 需解析 JSON 二次判断 |
| 纯数字（如 `1214`） | 未匹配到枚举的错误码 | 按错误码查 error-codes.md |

---

## 场景索引

| 场景编号 | 报错类型 | callback msg 特征 | 常见错误码 |
|---------|---------|-------------------|-----------|
| S1 | 转换失败（通用） | `conversion unknown` / JSON详情 | 1214, 520, 905 |
| S2 | 源文件下载失败 | `conversion download err` | 1001, 1014 |
| S3 | 转换超时 | `conversion timeout` | 495, 521, 1006, 503 |
| S4 | 加密文档 | `conversion invalid password` | 491, 492, 1004 |
| S5 | MIME/格式错误 | `conversion fotmat err` | 415, 529 |
| S6 | 转换引擎繁忙/错误 | `conversion soffice err` / ServerBusy | 493, 496, 1011, 505, 506 |
| S7 | 文件过大/内容超限 | `content toolarge` / `content limit` | 413, 901, 530-539 |
| S8 | ModelOp 失败 | `model op busy/fail/error` | — |
| S9 | 回调未收到 | 无回调 | — |
| S10 | 下载失败 | HTTP 响应错误 | 423-426, 502 |
| S11 | 入口鉴权/参数错误 | HTTP 同步返回（无 callback） | 400-421 |

---

## S1: 转换失败（通用）

**callback 特征**：
- `code`: `TaskFailNotify` / `ConvertFailNotify`
- `detail.msg`: `conversion unknown` 或 JSON 字符串

**日志特征**：
```
[ConvertWorker] Catch Exception from External Conversion service: Error: Error creating class
com.aspose.words.UnsupportedFileFormatException: Unsupported file format: Unknown
```
或：
```
[CLConvertor] ExitCode (code=<非0>;errCode:<非200>;id=<taskId>)
```
或：
```
[CommonJavaConvertor] Error: ...
```

**诊断要点**：
1. 搜索 taskId 找到 `[ConvertWorker] Catch Exception` 或 `[CLConvertor] ExitCode` 日志
2. 查看 Java 异常堆栈（java*.log），确认具体错误类型
3. 检查文件下载日志中的 `fileSize`，判断文件是否正常
4. 1214 是通用的转换失败码，**不能绑定到某个特定异常**，必须看日志确认真实原因

**引导用户验证**：
1. 获取业务系统调用下载文件的日志，以及文件下载到本地的日志
2. 如果下载的文件比较小（如 < 10KB），用文本编辑器打开看看内容——可能是 HTML 错误页面、XML 错误响应等非真实文件内容
3. 用本地 WPS/Office 打开下载的文件，看看是否能打开：
   - 如果打不开：可能文件内容被篡改或不完整，需要用户单独调用下载接口对比文件是否一致
   - 如果能打开但转换失败：可能包含不支持的特性（特殊加密、宏、ActiveX 等）
4. 检查文件扩展名与实际格式是否一致（日志搜索 `inconsistent` 或 `correct mime type`）

---

## S2: 源文件下载失败

**callback 特征**：
- `detail.msg`: `conversion download err`

**日志特征**：
```
[RemoteContentImpl] download file error
[ConvertFileService] [<docKey>] is failed to returned as source stream in <N>ms
```
或文件大小异常：
```
[LocalConvertServiceImp] tid:<taskId> fileSize is 0 B
downloadFile size is <N>B, is too small
```

**诊断要点**：
1. 搜索 `Download` + taskId，确认下载是否成功、文件大小、耗时
2. 常见原因：fileUrl DNS 失败(`ENOTFOUND`)、连接超时(`ETIMEDOUT`)、SSL 证书错误、HTTP 非 200
3. 下载超时硬编码 10s，无重试

**引导用户验证**：
1. 用 curl 直接请求 fileUrl，验证链接是否可正常下载
2. 对比 fileUrl 返回的文件大小与原始文件大小是否一致
3. 如果文件很小，用文本编辑器打开看内容（可能是 JSON/XML 错误响应而非真实文件）

---

## S3: 转换超时

**callback 特征**：
- `detail.msg`: `conversion timeout`

**日志特征**：
```
timeout exceeded
It's time to cancel task ... since time is over
ETIMEDOUT
```
或 IMPORTING 超时（opendoc 场景）：
```
importFailProcess
IMPORT_TASK_INCOMPLETE
```

**诊断要点**：
1. 搜索 taskId 确认任务接收时间和超时时间
2. 默认超时：Convert 3min、CL/Java 5min、ModelOp 5min、CAD 10min
3. 大文件/复杂文档容易超时
4. 如果任务没被 TaskServer 消费——检查队列积压

---

## S4: 加密文档

**callback 特征**：
- `detail.msg`: `conversion invalid password`

**日志特征**：
```
INVALID_PASSWORD
Password Protected
UNSUPPORTED_ENCRYPTION
```

**诊断要点**：
1. 491 = OOXML 加密文档未提供密码或密码错误
2. 492 = 不支持的加密方式（常见于 ODF 格式）
3. 1004 = 业务系统的远端文件加密，解密失败

---

## S5: MIME/格式错误

**callback 特征**：
- `detail.msg`: `conversion fotmat err`

**日志特征**：
```
Invalid File mime type
no supported mime type
correct mime type should be
```

**诊断要点**：
1. 文件扩展名与实际内容不匹配（如 .xlsx 实际是 CSV、.doc 实际是 HTML）
2. 搜索 `correct mime type` 看是否做了自动修正
3. MIME 检测通过 Java `FileTypeUtil`（Magic Bytes），不依赖扩展名

**引导用户验证**：
1. 检查文件是否被改名或格式转换过
2. 用文本编辑器打开看文件头部（前几个字节），确认实际格式

---

## S6: 转换引擎繁忙/错误

**callback 特征**：
- `detail.msg`: `conversion soffice err`（Symphony 引擎错误）

**API 同步返回**：
- `code`: `ServerBusy`（506）或 `TaskQueueCongestion`（505）

**日志特征**：
```
java standalone convertor is busy
get queue lock fail
maxActiveTask
concurrent exceeds license limit
SOFFICE_BUSY
```

**诊断要点**：
1. ServerBusy = Redis 队列锁获取失败或 addTask 返回 null
2. TaskQueueCongestion = 活跃任务数超过 maxActiveTask 限制
3. 1011 = Java Standalone / Puppeteer 实例全忙
4. 493 = Symphony 引擎全忙

---

## S7: 文件过大/内容超限

**callback 特征**：
- `detail.msg`: `content toolarge`

**日志特征**：
```
CONTENT_TOOLARGE
FILE_TOO_LARGE
is larger than the max size
CONTENT_LIMIT
```

**诊断要点**：
1. 对照转换场景的文件大小限制（Word/Sheet 50MB、PPT 100MB 等）
2. 内容超限（530-539）：行列数、单元格数、页数等超过 Java conversion-config.json 中的限制

---

## S8: ModelOp 失败

**callback 特征**：
- `detail.msg`: `model op busy` / `model op fail` / `model op error` / `export doc busy`

**日志特征**：
```
[HttpModelOpServiceImpl] model op busy
[OpenModelWorker] model op with error
[ModelManager] Worker [pid] is killed because of timeout!
[CanvasModelService] Print writer To PDF Fail
```

**诊断要点**：
1. `model op busy` = ModelOp 队列满，无法入队
2. `model op fail` = ModelOp 执行失败（ops 不支持或参数错误）
3. `model op error` = Canvas 渲染过程异常
4. `export doc busy` = 导出任务入队失败
5. 需同时搜索 step1（CONVERT）和 step2（MODEL_OP）两步日志

---

## S9: 回调未收到

**表现**：任务提交成功（拿到 taskId），但 callback 地址始终未收到通知

**日志特征**：
```
[AbstractOpenService] task notify with error
notify res fail with <status> <statusText>
failed to notify with wrong taskCtx
```

**诊断要点**：
1. 先确认任务是否已完成——搜索 taskId 看是否有 `Result of Conversion task`
2. 如果任务完成了但回调没收到——搜索 `notify` 看回调发送日志
3. 回调超时 5s 硬编码，且**无重试机制**，失败即丢失
4. 确认 callbackUrl 是否被白名单拦截（`CallbackUrlNotAllowed`）
5. 可用 `queryTaskStatus` 接口主动查询任务状态作为兜底

**引导用户验证**：
1. 确认 callback 接口是否能从转换服务器网络访问到
2. 确认 callback 接口是否能在 5s 内响应完毕
3. 使用 queryTaskStatus 接口查询当前任务状态

---

## S10: 下载失败

**表现**：收到成功回调后，调用下载接口报错

**下载接口返回的错误码**：

| `code` | 含义 | 常见原因 |
|--------|------|---------|
| `InvalidTaskId` (423) | taskId 无效或已过期 | 超过下载 TTL（默认 60min） |
| `ContentIdIsNull` (426) | 缺少 contentId 参数 | 请求参数遗漏 |
| `ContentIdError` (425) | contentId 对应文件不存在 | contentId 错误或存储异常 |
| `AccessOtherRepoIsNotAllowed` (424) | 无权下载 | appId 与任务归属不匹配 |
| `DownloadErr` (502) | 下载通用错误 | 存储连接异常 |

**诊断要点**：
1. 最常见的是 `InvalidTaskId`——超过 60 分钟 TTL
2. 确认 contentId 来自成功回调中的 `detail.contentId`
3. 下载接口不走 callback，错误直接在 HTTP 响应中返回

---

## S11: 入口鉴权/参数校验错误

**表现**：提交接口同步返回错误（HTTP 401/412/200+code），无 taskId，不触发 callback

**HTTP 401 — 鉴权失败**：
| 可能原因 | 说明 |
|---------|------|
| HMAC 签名不匹配 | 检查 secret、timestamp（±30s）、nonce、rawBody |
| repo 未启用 PublicAPI | 检查 repo 配置 |
| Authorization 格式错误 | 必须是 `repoId:appId:token` 三段式 |

**HTTP 412 — 参数校验失败**：
| 常见错误 | 说明 |
|---------|------|
| `FilenameIsNull` | filename 参数为空 |
| `FileUrlNotAllowed` | fileUrl host 不在白名单 |
| `CallbackUrlNotAllowed` | callback host 不在白名单 |
| `DocTypeNotSupport` | 不支持的文件类型转换 |
| 水印参数错误（WM*） | 水印字段缺失或超范围 |

**诊断要点**：
1. 这类错误在提交时同步返回，不产生 taskId，不会有 callback
2. 看 HTTP 响应的 `code` 字段即可定位具体原因
3. 鉴权错误最常见：timestamp 偏差、secret 错误、body 签名不一致
