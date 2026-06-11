---
name: openApi故障分析
description: 分析 luoshu-server OpenAPI 错误日志，覆盖鉴权 → 参数校验 → 任务入队 → 源文件下载 → 文件校验 → 转换执行 → 回调通知 → 下载全链路。当用户提到 OpenAPI 报错、转换失败、回调失败、任务超时、ServerBusy、下载失败时触发。报错场景详细诊断见 references/openapi-error-scenarios.md。
disable-model-invocation: true
---

# OpenAPI 错误日志分析

## 用户应提供的信息

| 优先级 | 信息 | 说明 |
|--------|------|------|
| **必须** | 错误发生的大概时间 | 精确到分钟最佳 |
| **强烈建议** | taskId | OpenAPI 任务唯一标识 |
| **强烈建议** | 错误现象描述 | 如"回调收到 FAIL"、"下载报错"、"一直 IN_QUEUE" |
| 建议 | callback 收到的内容 | code、detail.msg 字段值 |
| 建议 | 调用的接口路径 | /convert、/content/ops、/merge 等 |

---

## 快速定位：通过 callback 判断

> **核心原则**：先看 callback（或 queryTaskStatus）返回的 `code` + `detail.msg`，可以快速定位到错误类型，再去日志中确认细节。

### callback body 格式

```json
{
  "taskId": "uuid",
  "code": "TaskFailNotify",
  "detail": {
    "taskStatus": "FAIL",
    "msg": "conversion unknown"
  }
}
```

### code 字段速判

| `code` | 含义 | 下一步 |
|--------|------|--------|
| `TaskSuccessNotify` / `ConvertSuccessNotify` | 成功 | 用 contentId 下载结果 |
| `TaskFailNotify` / `ConvertFailNotify` | 失败 | 看 `detail.msg` |
| `TaskHandingNotify` | 处理中 | 等待或排查超时 |
| `InvalidTaskId` | taskId 无效/过期 | 超过 60min TTL |

### detail.msg 快速分类

| msg 关键词 | 含义 | 场景 |
|-----------|------|------|
| `conversion unknown` | 转换未知错误 | 看 Java 异常堆栈 |
| `conversion timeout` | 转换超时 | 文件过大或引擎繁忙 |
| `conversion download err` | 源文件下载失败 | fileUrl 不可达 |
| `conversion invalid password` | 加密文档 | 需密码或不支持的加密 |
| `conversion fotmat err` | 格式/MIME 错误 | 扩展名与实际格式不符 |
| `conversion soffice err` | Symphony 引擎错误 | 引擎崩溃/超时 |
| `content toolarge` | 文件过大 | 超出大小限制 |
| `model op busy/fail/error` | ModelOp 失败 | 队列满或执行异常 |

> **报错场景的详细诊断指南见 `references/openapi-error-scenarios.md`**

---

## 请求全链路（简要）

```
① 鉴权 → HMAC签名 + License + Quota
② 参数校验 → filename/fileUrl/callback/水印/白名单
③ 任务入队 → Redis锁 + 限流 + Bull队列
④ 源文件下载(TaskServer) → fileUrl HTTP下载(10s超时) 或 docId本地拷贝
⑤ 文件校验 → 空文件/大小/MIME/加密检测
⑥ 转换执行 → CL/Aspose/Canvas/Puppeteer/JavaSA 等引擎
⑦ 回调通知 → POST callbackUrl(5s超时，无重试)
⑧ 下载 → taskId + contentId 获取结果文件
```

---

## 按 taskId 定位

```bash
LOG_DIR=/opt/zdocs/luoshu_log

# 全节点搜索 taskId，得到完整时间线
rg '<taskId>' $LOG_DIR/*/{combined,error,java}*.log* | sort

# 关键事件标志：
# [convertReqHandler] ... code=Ok           → 入队成功
# [Task Server] Receive Task                → TaskServer 接收
# [LocalConvertServiceImp] Download ... done → 下载完成
# [ConvertWorker] Result ... [1299]          → 转换成功
# [ConvertWorker] Result ... [非1299]        → 转换失败
# [AbstractOpenService] notify ... Success   → 回调成功
# [AbstractOpenService] task notify with error → 回调失败
```

---

## 阶段定位：根据现象判断

| 现象 | 所在阶段 | 日志关键字 |
|------|---------|-----------|
| HTTP 401 | ① 鉴权 | `verify token fail` |
| HTTP 412 + ResCode | ② 参数校验 | ResCode 名称 |
| HTTP 200 + ServerBusy | ③ 入队 | `get queue lock fail` |
| callback 收到 FAIL | ④⑤⑥ 下载/校验/转换 | `[ConvertWorker]` |
| callback 未收到 | ⑦ 回调失败 | `task notify with error` |
| 下载报错 | ⑧ 下载 | `[downloadResult]` |

---

## 日志搜索速查

```bash
LOG_DIR=/opt/zdocs/luoshu_log

# ═══ 通用 ═══
rg '<taskId>' $LOG_DIR/*/{combined,error,java}*.log* | sort

# ═══ ① 鉴权 ═══
rg 'verify token fail|Token format is invalid|Secret.*is null|Public API is disabled' $LOG_DIR/*/combined*.log*
rg 'License not found|License.*expired' $LOG_DIR/*/combined*.log*

# ═══ ③ 入队 ═══
rg 'get queue lock fail|maxActiveTask|concurrent exceeds license limit' $LOG_DIR/*/combined*.log*

# ═══ ④ 下载 ═══
rg 'download error|CONVERT_DOWNLOAD_ERROR|0 bytes|is too small' $LOG_DIR/*/{combined,error}*.log*

# ═══ ⑤ 校验 ═══
rg 'Invalid File mime type|Password Protected|FILE_TOO_LARGE' $LOG_DIR/*/{combined,error}*.log*

# ═══ ⑥ 转换 ═══
rg 'Result of Conversion task' $LOG_DIR/*/combined*.log*
rg '\[ConvertWorker\].*Catch Exception' $LOG_DIR/*/{combined,error}*.log*
rg '\[CLConvertor\].*ExitCode' $LOG_DIR/*/{combined,error}*.log*
rg 'ERROR|Exception|OutOfMemory' $LOG_DIR/*/java*.log*
# ModelOp
rg 'model op busy|model op fail|model op error' $LOG_DIR/*/{combined,error}*.log*
rg '\[ModelManager\].*killed|Worker.*error' $LOG_DIR/*/{combined,error}*.log*

# ═══ ⑦ 回调 ═══
rg '\[AbstractOpenService\].*notify' $LOG_DIR/*/{combined,error}*.log*
rg 'notify res fail|task notify with error' $LOG_DIR/*/{combined,error}*.log*

# ═══ ⑧ 下载接口 ═══
rg '\[downloadResult\]' $LOG_DIR/*/{combined,error}*.log*

# ═══ Java 层 ═══
rg 'ERROR|Exception|OutOfMemory' $LOG_DIR/*/java*.log*
```

---

## ResCode 速查（HTTP 同步返回）

### 鉴权错误（HTTP 401）

| ResCode | 值 | 触发条件 |
|---------|------|---------|
| `InvalidAuthHeader` | 400 | HMAC 签名不匹配 |
| `InvalidAuthRepoID` | 401 | repo 未启用 PublicAPI |
| `InvalidAuthTimestamp` | 402 | 时间戳差 > 30s |
| `TokenIsInvalid` | 403 | Authorization 非三段格式 |
| `PublicApiIsDisable` | 464 | PublicAPI 未启用 |

### 参数校验错误（HTTP 412）

| ResCode | 值 | 触发条件 |
|---------|------|---------|
| `FilenameIsNull` | 410 | filename 为空 |
| `CallbackIsNull` | 414 | callback 为空 |
| `FileUrlNotAllowed` | 419 | fileUrl 不在白名单 |
| `CallbackUrlNotAllowed` | 420 | callback 不在白名单 |
| `DocTypeNotSupport` | 421 | 不支持的转换类型 |

### 入队错误（HTTP 200 + code）

| ResCode | 值 | 触发条件 |
|---------|------|---------|
| `TaskQueueCongestion` | 505 | 活跃任务数超限 |
| `ServerBusy` | 506 | Redis 锁失败/CPU License 超限 |
| `NotSupportTask` | 513 | 转换类型不支持 |

### 回调/下载错误码

| ResCode | 值 | 用途 |
|---------|------|------|
| `TaskSuccessNotify` | 509 | 成功回调 code |
| `TaskFailNotify` | 510 | 失败回调 code |
| `InvalidTaskId` | 423 | taskId 过期（默认 60min TTL） |
| `ContentIdError` | 425 | contentId 对应文件不存在 |

---

## ConvertErrCode 速查（TaskServer 转换层）

| Code | 值 | 含义 |
|------|----|------|
| `CONVERSION_DONE` | 200 | 转换完成 |
| `FILE_TOO_LARGE` | 413 | 文件过大 |
| `FILE_INVALID_MIMETYPE` | 415 | MIME 无效 |
| `INVALID_PASSWORD` | 491 | 加密文档 |
| `SOFFICE_BUSY` | 493 | Symphony 引擎全忙 |
| `UNKNOWN` | 520 | 未知错误 |
| `CL_TIMEOUT` | 521 | CL 转换超时 |
| `CORRUPTED_FILE` | 529 | 文件损坏 |
| `CONTENT_LIMIT` | 901 | 内容超限 |
| `CONVERSION_UNKNOWN` | 905 | 聚合未知错误 |
| `CONVERT_DOWNLOAD_ERROR` | 1001 | 下载失败 |
| `CONVERT_STANDALONE_BUSY` | 1011 | Java/Puppeteer 实例全忙 |
| `DOWNLOAD_FILE_TOO_SMALL` | 1014 | 下载文件过小 |

> 完整错误码列表见原始文档中的 ConvertErrCode 章节。

---

## 任务判定规则

| 判定依据 | 成功 | 失败 |
|---------|------|------|
| `Result of Conversion task [<tid>]: [<code>]` | code = 1299 | code ≠ 1299 |
| `Done the execution ... with code [<code>]` | code = 200 | code ≠ 200 |

---

## 超时配置

| 组件 | 默认值 |
|------|--------|
| Bull 任务超时 | 180s (3min) |
| CL/Java 转换超时 | 300s (5min) |
| ModelOp 超时 | 300s (5min) |
| JavaSA 锁等待 | 120s |
| 源文件下载超时 | 10s |
| 回调通知超时 | 5s（无重试） |
| 下载 Redis TTL | 60min |

---

## 文件大小限制

| 转换场景 | 限制 |
|---------|------|
| Word → PDF | 50MB |
| Sheet → PDF | 50MB |
| PPT → PDF | 100MB |
| 水印图片 | 50MB |
| merge 总大小 | 400MB |
| OOXML 最小合法 | 1KB |

---

## 脚本辅助

```
docs/skills/scripts/analyze_openapi_failure.py
```

```bash
# 按 taskId 分析
python3 docs/skills/scripts/analyze_openapi_failure.py --logDir <LOG_DIR> --taskId <taskId>

# 按时间段分析失败任务
python3 docs/skills/scripts/analyze_openapi_failure.py --logDir <LOG_DIR> --timeFrom "2026-05-22 14:00:00" --timeTo "2026-05-22 15:00:00"
```

报错场景诊断：`references/openapi-error-scenarios.md`
错误码参考：`references/error-codes.md`
