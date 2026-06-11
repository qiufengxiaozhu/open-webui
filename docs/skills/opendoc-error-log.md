---
name: 文档打开故障分析
description: 分析洛书服务（luoshu-server）文档打开（预览+编辑）错误日志。覆盖 HTTP 入口（认证/License/权限/格式校验）→ WebSocket 连接 → 文档转换（源文件下载/CL转版/Aspose转版/draft存储）→ 保存发布 → 资源加载全阶段。当用户提到文档打不开、预览白屏、编辑报错、转换失败、保存失败、导出错误、WebSocket断线、draft加载失败、资源404、IMPORTING超时时触发。报错场景详细诊断见 references/opendoc-error-scenarios.md。
disable-model-invocation: true
---

# 洛书文档打开（预览+编辑）错误日志分析

## 用户应提供的信息

| 优先级 | 信息 | 说明 |
|--------|------|------|
| **必须** | 错误发生的大概时间 | 精确到分钟最佳 |
| **必须** | docId 或 taskId | 文档或转换任务的唯一标识 |
| **强烈建议** | 错误现象描述 | 如"白屏"、"转换服务不可用"、"一直转圈" |
| 建议 | 文件类型 | docx/xlsx/pptx/pdf/ofd 等 |
| 建议 | 前端错误截图 | 页面上的 Error Code 等 |

---

## 请求全链路（简要）

```
① HTTP 入口 → 认证/License/权限/格式校验 → 成功渲染页面 / 失败渲染 error.ejs
② WebSocket 连接 → JWT校验 + 连接数限制 → 连接成功
③ 文档打开命令 → meta 状态判断 → INACTIVE 触发转换 / ACTIVE 直接加入 / ERROR 返回错误
④ 文档转换(TaskServer) → 下载源文件 → CL/Aspose 转换 → 上传结果 → 回调 DocsServer
⑤ Draft 加载 → 前端拉取 JSON/PDF draft
⑦ 保存/发布 → applyMsg → convert → uploadNewVersion
⑧ 资源加载 → 图片/字体/WMF懒转换
```

---

## 按 docId / taskId 定位

### 按 docId 全链路串联（最常用）

```bash
LOG_DIR=/opt/zdocs/luoshu_log

# 全节点搜索 docId，得到完整时间线
rg '<docId>' $LOG_DIR/*/{combined,error}*.log* | sort

# 如果有 taskId，再搜 TaskServer 日志
rg '<taskId>' $LOG_DIR/TaskServer_*/{combined,error,java}*.log* | sort
```

### 按时间段扫描（无 docId/taskId 时）

```bash
# 搜索该时间段内的转换失败
rg '\[ConvertWorker\] Result' $LOG_DIR/TaskServer_*/combined*.log* | rg '14:30' | rg -v '1299'

# 搜索该时间段内的文档打开错误
rg 'errorCode|error\.ejs|importError' $LOG_DIR/NewDocServer_*/combined*.log* | rg '14:30'
```

---

## 阶段定位：根据现象判断

| 现象 | 所在阶段 | 关键日志前缀 |
|------|---------|-------------|
| 浏览器显示错误页面（error.ejs） | ① HTTP 入口 | `[apigateway_api_docs]` |
| 打开文档后白屏/转圈不动 | ②③④ | `[ConnectionManager]` `[DocumentService]` |
| 提示"转换服务不可用" + Error Code | ④ 转换失败 | `[ConvertWorker]` |
| "无法打开文档" + Error Code: 1203 | ④ MIME/格式校验失败 | `[DocumentService] convert import error` |
| 提示"文档正在转换中"但一直不结束 | ④ IMPORTING超时 | `[importFailProcess]` |
| 提示"无权编辑" | ①③ 权限检查 | `[apigateway_api_docs]` |
| License 相关错误 | ① License | `[apigateway_api_docs]` |
| 保存失败 | ⑦ 保存/发布 | `[ExportDocWorker]` |
| 图片显示不出来 | ⑧ 资源加载 | `[attachment]` |
| draft 加载失败 | ⑤ Draft | `[DraftStorageService]` |

> **报错场景的详细诊断指南见 `references/opendoc-error-scenarios.md`**

---

## 日志搜索速查

```bash
LOG_DIR=/opt/zdocs/luoshu_log

# ═══ ① HTTP 入口层 ═══
rg 'errorCode|error\.ejs' $LOG_DIR/*/combined*.log*
rg 'NO_RIGHT_EDIT_FILE|FILE_NOT_FOUND|DOC_TYPE_NOT_SUPPORT' $LOG_DIR/*/combined*.log*
rg 'LICENSE_EXPIRE|LICENSE_NOT_AVAIL' $LOG_DIR/*/combined*.log*

# ═══ ② WebSocket ═══
rg 'AUTH_OTHER_ERROR|SESSION_EXPIRE|REACH_MAX' $LOG_DIR/*/{combined,error}*.log*

# ═══ ③ 文档打开 ═══
rg 'startConvert|needConvert|importFailProcess' $LOG_DIR/*/combined*.log*
rg 'CONTENT_TOOLARGE|IMPORT_TASK_INCOMPLETE' $LOG_DIR/*/combined*.log*

# ═══ ④ 文档转换 ═══
rg 'Result of Conversion task' $LOG_DIR/*/combined*.log*
rg '\[ConvertWorker\].*Catch Exception' $LOG_DIR/*/{combined,error}*.log*
rg '\[CLConvertor\].*ExitCode' $LOG_DIR/*/{combined,error}*.log*
rg 'ERROR|Exception' $LOG_DIR/*/java*.log*
rg 'importDone|importError' $LOG_DIR/*/combined*.log*
# 文件下载相关
rg '\[LocalConvertServiceImp\].*Download' $LOG_DIR/*/combined*.log*
rg 'fileSize' $LOG_DIR/*/combined*.log*

# ═══ ⑤ Draft 加载 ═══
rg 'Could not find draft|Could not get draft' $LOG_DIR/*/{combined,error}*.log*

# ═══ ⑦ 保存/发布 ═══
rg 'PUBLISH_REMOTE_ERROR|Could not upload' $LOG_DIR/*/{combined,error}*.log*
rg 'applyMessages.*error' $LOG_DIR/*/{combined,error}*.log*

# ═══ ⑧ 资源加载 ═══
rg 'getResource.*not find|resourceConvert' $LOG_DIR/*/combined*.log*
```

---

## 转换结果错误码

### Result Code

| code | 含义 |
|------|------|
| 1299 | 成功（可能附带 detail 警告） |
| 1214 | 转换失败（通用码，可能是 OOM/引擎崩溃/格式不支持等，必须看日志确认原因） |

### DocsErrorCode 速查（转换相关）

| DocsErrorCode | 来源 ConvertErrCode | 含义 |
|---------------|---------------------|------|
| `CONVERSION_NOERR` | 0, 200, 501, 906 | 成功（含警告） |
| `CONVERSION_UNKNOWN` | 520, 525, 526 等 + 1215-1299 归一 | 未知转换错误 |
| `CONVERSION_TIMEOUT` | 495, 521, 1006 | 转换超时 |
| `CONVERSION_FOTMAT_ERR` | 415, 529 | MIME/文件格式错误 |
| `CONVERSION_DOWNLOAD_ERR` | 1001 | 源文件下载失败 |
| `CONVERSION_UPLOAD_ERR` | 1002 | 结果上传失败 |
| `CONVERSION_INVALID_PASSWORD` | 491 | 加密文档密码错误 |
| `CONVERSION_UNSUPPORTED_ENCRYPTION` | 492 | 不支持的加密方式 |
| `CONVERSION_SERVER_BUSY` | 493, 1011 | 转换引擎繁忙 |
| `CONVERSION_CL_ERR` | 522-524, 527, 528 | CL/OOXML 转换错误 |
| `CONVERSION_CAD_ERR` | 1005 | CAD 转换错误 |
| `CONTENT_TOOLARGE` | 413 | 文件过大 |
| `CONTENT_LIMIT` | 901 (530-539) | 内容超限 |
| `DOWNLOAD_FILE_TOO_SMALL` | 1014 | 下载文件过小 |

> 1215-1299 范围的错误码会被 `ErrorUtil` 统一归一为 `CONVERSION_UNKNOWN`。

---

## Meta 状态机

| MetaStatus | 行为 | 错误码 |
|------------|------|--------|
| `INACTIVE` | 触发转换 | CONVERSION_NOT_START（入队失败） |
| `IMPORTING` | 等待中；3min 后 importFailProcess | IMPORT_TASK_INCOMPLETE（重试3次后） |
| `ACTIVE` | 直接加入会话 | — |
| `ERROR` | 返回持久化的 errorCode | 持久化错误码 |
| `CONFLICT` | 版本冲突 | DRAFT_VERSION_CONFLICT |

---

## 任务判定规则

| 判定依据 | 成功 | 失败 |
|---------|------|------|
| `Result of Conversion task [<tid>]: [<code>]` | code = 1299 | code ≠ 1299 |
| `Done the execution ... with code [<code>]` | code = 200 | code ≠ 200 |

> CLConvertor ExitCode `code=112; errCode:412` 可能伴随 `parser error`，但若最终 Result code 为 1299 则任务**成功**。

---

## 超时配置

| 组件 | 默认值 |
|------|--------|
| Convert 任务超时 | 180s (3min) |
| ExportDoc 超时 | 300s (5min) |
| CL/Java 转换超时 | 300s (5min) |
| IMPORTING 超时 | 3min/次，最多3次 |
| IMPORTING 最大超时 | 60min |
| WebSocket disconnect→leave | 60s |

---

## 文件大小限制

| 文档类型 | 编辑限制 | 预览限制 |
|---------|---------|---------|
| Word | 300MB | 同上或更大 |
| Sheet | 300MB | 同上 |
| PPT | 100MB | 同上 |
| PDF | 200MB | 300MB |

---

## 脚本辅助

```
docs/skills/scripts/analyze_task_failure.py
```

```bash
# 按 docId 分析
python3 docs/skills/scripts/analyze_task_failure.py --logDir <LOG_DIR> --docId <docId>

# 按 taskId 分析
python3 docs/skills/scripts/analyze_task_failure.py --logDir <LOG_DIR> --taskId <taskId>

# 按时间段分析失败任务
python3 docs/skills/scripts/analyze_task_failure.py --logDir <LOG_DIR> --timeFrom "2026-05-22 14:00:00" --timeTo "2026-05-22 15:00:00"
```

错误码参考：`docs/skills/references/error-codes.md`
报错场景诊断：`docs/skills/references/opendoc-error-scenarios.md`
