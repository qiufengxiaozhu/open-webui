# 文档打开报错场景速查

> 本文档归纳了文档打开/预览过程中各类报错的典型场景、日志特征和诊断要点。
> 诊断时应先确定报错属于哪个场景，再按对应的日志特征进行搜索验证。

---

## 场景索引

| 场景编号 | 报错类型 | 前端表现 | 常见错误码 |
|---------|---------|---------|-----------|
| S1 | 文件格式不支持 / 文件损坏 | "转换服务不可用" | 1214 |
| S1a | MIME 类型校验失败 / 扩展名与实际格式不匹配 | "无法打开文档"，Error Code: 1203 | 1203 (detail.id=415) |
| S2 | 源文件下载异常 | "转换服务不可用" | 1214, 1001, 1014 |
| S3 | 转换引擎 OOM | "转换服务不可用" | 1214 |
| S4 | 转换超时 | 一直转圈 / IMPORTING 超时 | 495, 521, 1006 |
| S5 | 加密文档 | 提示输入密码 / 打开失败 | 491, 492, 1004 |
| S6 | 文件过大 / 内容超限 | "文件过大" | 413, 901, 530-539 |
| S7 | 权限 / 认证错误 | "无权访问" / 错误页面 | HTTP 入口层错误码 |
| S8 | WebSocket 连接失败 | 白屏 / 断线 | AUTH_OTHER_ERROR 等 |
| S9 | License 异常 | "License 过期" 等 | LICENSE_* |

---

## S1: 文件格式不支持 / 文件损坏

**前端表现**：页面显示"转换服务不可用"，Error Code: 1214

**日志特征**：
```
[ConvertWorker] Catch Exception from External Conversion service: Error: Error creating class
com.aspose.words.UnsupportedFileFormatException: Unsupported file format: Unknown
```
或：
```
[CLConvertor] stderr: Entity: line 1: parser error : Document is empty
```
或：
```
[ConvertWorker] Catch Exception from External Conversion service: Error: Error creating class
com.aspose.slides.exceptions.PptxReadException: ...
```

**诊断要点**：
1. 搜索 taskId 找到 `[ConvertWorker] Catch Exception` 或 `[CLConvertor] stderr` 日志
2. 查看 Java 异常堆栈，确认是哪种格式异常（Aspose Words/Cells/Slides/PDF）
3. 查看文件下载日志中的 `fileSize`，确认文件大小是否正常
4. **核心判断**：文件本身是否有问题

**引导用户验证**：
1. 获取业务系统调用下载文件的日志，以及文件下载到本地的日志
2. 如果下载的文件比较小（如 < 10KB），用文本编辑器打开看看内容，可能包含错误信息（如 HTML 错误页面、XML 错误响应等）
3. 用本地 WPS/Office 打开下载的文件，看看是否能打开：
   - 如果打不开：可能是文件内容被篡改或不完整，需要用户单独调用下载接口对比文件是否一致
   - 如果能打开但转换失败：可能是文档包含不支持的特性（如特殊加密、宏、ActiveX 控件等）
4. 检查文件扩展名与实际格式是否一致（日志中搜索 `inconsistent`）

---

## S1a: MIME 类型校验失败 / 扩展名与实际格式不匹配

**前端表现**：页面显示"无法打开文档"，Error Code: 1203

**错误码说明**：1203 对应 `CONVERSION_FOTMAT_ERR`（来源 ConvertErrCode 415 / 529）

**日志特征**：
```
[DocumentService] ifcp:<docId> convert import error!
```
转换响应中包含：
```
"isSucceed":false,"detail":[{"id":415,"description":"Conversion - Invalid File Mime Type","parameters":{"correctSourceMIMEType":""}}]
```
CL 转换器层面通常伴随：
```
[CLConvertor] stderr: Entity: line 1: parser error : Document is empty
[CLConvertor] error spawnAsync ... ooxmlconvertor exited with non-zero code: 112
```

**典型案例**（2026-05-21 安装tcpdump.docx）：
- 扩展名为 `.docx`，但文件内容无法被识别为合法 Office 文档
- `correctSourceMIMEType` 字段为空 — 引擎无法识别任何 MIME 类型
- CLConvertor 解析时报 `Document is empty`，ooxmlconvertor 子进程退出码 112
- 同日该模式批量复现 243 次，说明可能是存储/下载环节批量问题

**诊断要点**：
1. 搜索 docId 找到 `convert import error` + `error=1203` 日志
2. 检查转换响应中 `detail` 的 `id` 字段：`415` = MIME 不匹配，`529` = 文件损坏
3. 检查 `correctSourceMIMEType` 字段是否为空（为空说明完全无法识别格式）
4. 搜索 `Document is empty` 或 `ExitCode` 确认 CL 转换器层的具体错误
5. 如果同时间段大量文件出现同样错误，可能是存储/下载链路批量异常

**引导用户验证**：
1. 从源存储下载原始文件，用 WPS / Microsoft Office 打开 — 若提示"文件已损坏"或显示空白，则文件本身损坏
2. 检查文件大小（`ls -la`），健康 docx 通常至少几 KB；用 `xxd 文件 | head` 检查首字节是否为 `PK\x03\x04`（ZIP 魔数，docx 本质是 ZIP 包）— 若首字节不是 PK 或文件 < 1KB，则文件不合法
3. 核对文件扩展名与实际格式 — 可能是将文本/图片/HTML 等非 Office 文件直接改名为 `.docx`
4. 如果同一仓库多个文件同时报此错误，排查业务系统的文件上传/存储/下载接口是否返回了错误内容（如 HTML 错误页面而非真实文件）

---

## S2: 源文件下载异常

**前端表现**：页面显示"转换服务不可用"，Error Code: 1214 或 1001

**日志特征**：
```
[ConvertFileService] [<docKey>] is failed to returned as source stream in <N>ms
```
或下载成功但文件过小：
```
[LocalConvertServiceImp] tid:<taskId> fileSize is <极小值> B
```
或下载文件过小被拦截：
```
downloadFile size is <N>B, less than <limit>KB
```

**诊断要点**：
1. 搜索 `Download` + taskId，查看文件下载是否成功、耗时多少
2. 检查 `fileSize` 是否异常（正常的文档一般至少几 KB）
3. 如果下载成功但大小为 0 或极小，说明源文件有问题或下载接口返回了错误内容

**常见原因**：
- 业务系统下载接口返回了 HTML 错误页面而非文件内容
- 业务系统对文件做了加密，下载时需要额外的解密参数
- 文件已从存储中被删除
- 网络超时导致下载不完整

**引导用户验证**：
1. 检查业务系统的文件下载接口是否正常（直接 curl 调用试试）
2. 对比业务系统存储的文件大小与转换服务下载到本地的文件大小
3. 如果文件很小，用文本编辑器打开看内容（可能是 JSON/XML 错误响应）

---

## S3: 转换引擎 OOM (Java 堆内存溢出)

**前端表现**：页面显示"转换服务不可用"，Error Code: 1214

**日志特征**：
```
[PDFMergeConvertor] Convert task [<tid>] is failed in [<N>]ms on error Error: Error running instance method
java.lang.OutOfMemoryError: Java heap space
```
或：
```
java.lang.OutOfMemoryError: GC overhead limit exceeded
```

**诊断要点**：
1. 搜索 `OutOfMemoryError` + 时间窗口，确认 OOM 发生时间与任务时间吻合
2. 检查文件大小 `fileSize`——OOM 通常出现在大文件（>50MB）或复杂文档上
3. 同一时间段如果有大量 OOM，说明是系统级内存不足

---

## S4: 转换超时

**前端表现**：一直转圈 / "文档正在转换中" 但始终不结束

**日志特征**：
```
timeout exceeded
Timed out
ETIMEDOUT
```
或 IMPORTING 超时：
```
importFailProcess
IMPORT_TASK_INCOMPLETE
```

**诊断要点**：
1. 搜索 taskId 在 TaskServer 日志中，查看任务是否被接收
2. 如果任务未被接收——检查 Bull 队列是否积压（搜索 `threshold|paused`）
3. 如果任务在执行中超时——检查文件大小和转换引擎日志
4. IMPORTING 超时策略：3min 触发 importFailProcess，最多重试 3 次

---

## S5: 加密文档

**前端表现**：提示输入密码 / 打开失败

**日志特征**：
```
CONVERSION_INVALID_PASSWORD
```
或：
```
CONVERSION_UNSUPPORTED_ENCRYPTION
```
或远端加密解密失败：
```
FETCH_REMOTE_FILE_DECRYPT_ERROR
```

**诊断要点**：
1. 491 = 密码错误或未提供密码
2. 492 = 不支持的加密方式（常见于 ODF 格式的加密）
3. 1004 = 业务系统的文件加密，解密密钥不对或接口异常

---

## S6: 文件过大 / 内容超限

**前端表现**：提示"文件过大"

**日志特征**：
```
CONTENT_TOOLARGE
```
或内容超限：
```
CONTENT_LIMIT
```

**诊断要点**：
1. 检查文件大小与配置限制的对比
2. 内容超限（901/530-539）：页数、行数、列数、单元格数超过限制
3. 预览模式的限制通常比编辑模式更宽松

**默认限制**：
- Word: 300MB | Sheet: 300MB | PPT: 100MB | PDF: 200MB(编辑)/300MB(预览)

---

## S7: 权限 / 认证错误

**前端表现**：显示错误页面（error.ejs），提示"无权访问"等

**日志特征**：
```
[apigateway_api_docs] ... errorCode
```
或：
```
error.ejs
```

**常见错误码**：
- `NO_RIGHT_EDIT_FILE` — 无编辑权限
- `EC_REPO_NOVIEWPERMISSION` — 无预览权限
- `FILE_NOT_FOUND` — 文件不存在
- `DOC_TYPE_NOT_SUPPORT` — 文件类型不支持

**诊断要点**：
1. 搜索 `[apigateway_api_docs]` + docId 查看 HTTP 入口层日志
2. 确认用户 token 是否有效
3. 确认文件类型是否在支持列表中

---

## S8: WebSocket 连接失败

**前端表现**：白屏 / 一直加载 / 断线

**日志特征**：
```
[ConnectionManager] auth error
AUTH_OTHER_ERROR
SESSION_EXPIRE
REACH_MAX_CONNECTIONS
```

**诊断要点**：
1. `AUTH_OTHER_ERROR` — token/clientId 为空或异常
2. `SESSION_EXPIRE` — JWT 过期
3. `REACH_MAX_CONNECTIONS_*` — 连接数超限（全局/单用户/单文档）
4. 检查是否有负载均衡 / 代理的 idle timeout 导致连接被切断

---

## S9: License 异常

**前端表现**：提示 License 相关错误

**日志特征**：
```
LICENSE_EXPIRE
LICENSE_NOT_AVAIL
LICENSE_EXCEED_USERS
NOT_ENABLE_EDIT
NOT_ENABLE_PREVIEW
```

**诊断要点**：
1. License 过期 → 需要续期
2. 用户数超限 → 需要扩容或清理不活跃用户
3. 能力未开启 → License v5 中编辑/预览能力需要单独授权
