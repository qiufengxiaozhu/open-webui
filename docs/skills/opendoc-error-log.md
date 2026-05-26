---
name: luoshu-opendoc-error-log
description: 分析洛书服务（luoshu-server）文档打开（预览+编辑）全链路错误日志。覆盖 HTTP 入口（认证/License/权限/格式校验）→ WebSocket 连接（握手/限流/心跳）→ 文档转换（源文件下载/MIME校验/CL转版/预转版/draft存储）→ OT 协同编辑（消息冲突/seq gap/重连）→ 保存发布（applyMsg/导出/回写/版本冲突）→ 资源加载（图片/字体/WMF懒转换）全阶段。当用户提到文档打不开、预览白屏、编辑报错、转换失败、协同异常、保存失败、导出错误、WebSocket断线、OT冲突、draft加载失败、资源404、IMPORTING超时时触发。
disable-model-invocation: true
---

# 洛书文档打开（预览+编辑）错误日志分析（完整版）

## 用户应提供的信息

分析文档打开/编辑错误日志时，用户需尽可能提供以下信息，以便快速定位问题：

| 优先级 | 信息 | 说明 | 示例 |
|--------|------|------|------|
| **必须** | 错误发生的大概时间 | 用于缩小日志搜索范围（精确到分钟最佳） | `2026-05-22 14:30` 左右 |
| **必须** | docId | 文档的唯一标识，URL 中可获取 | `abc123def456` |
| **强烈建议** | 错误现象描述 | 如"白屏"、"一直转圈"、"保存失败"、"图片不显示" | — |
| **强烈建议** | 操作模式 | 预览还是编辑 | 编辑模式 |
| 建议 | 文件类型 | docx/xlsx/pptx/pdf 等 | `.docx` |
| 建议 | taskId | 如果涉及转换任务，日志中可搜到 | — |
| 建议 | 前端错误截图 | 页面上的错误提示信息 | — |
| 有则提供 | repoId | 文档所属仓库 ID | — |
| 有则提供 | 用户标识 | email 或 userId，用于排查权限/连接问题 | — |
| 有则提供 | 浏览器控制台错误 | F12 Console 中的错误信息 | — |

> **为什么时间很重要**：文档打开涉及 HTTP 入口、WebSocket 连接、文档转换、OT 协同等多个环节，日志量极大。不提供时间范围将导致搜索范围过广。

---

## 按 docId / taskId 定位问题

### 方法一：按 docId 全链路串联（最常用）

docId 是贯穿文档打开全链路的核心标识，从 HTTP 入口到 WebSocket 连接、转换任务、OT 消息、保存发布的所有日志都会包含该 ID。

```bash
LOG_DIR=/opt/zdocs/luoshu_log

# 1. 全节点搜索 docId 的所有日志（按时间排序），得到完整时间线
rg '<docId>' $LOG_DIR/*/{combined,error}*.log* | sort

# 2. 根据时间线判断阶段：
#    - 有 [apigateway_api_docs] → ① HTTP 入口
#    - 有 [ConnectionManager] → ② WebSocket 连接
#    - 有 [DocumentService] openDoc/viewDoc → ③ 文档打开
#    - 有 [ConversionService] addConversionTask → ④ 转换入队
#    - 有 [ConvertWorker] Result → 转换结果
#    - 有 importDone/importError → 转换完成/失败
#    - 有 [OTDocument] → ⑥ OT 协同
#    - 有 [DocumentService] export/publish → ⑦ 保存/发布

# 3. 如果 DocsServer 日志中有 taskId，再按 taskId 搜 TaskServer 日志
rg '<taskId>' $LOG_DIR/TaskServer_*/{combined,error,java}*.log* | sort
```

**docId 日志时间线示例**：

```
14:30:01  [apigateway_api_docs] openDoc docId=doc123              ← ① HTTP 入口
14:30:01  [DocumentService] meta status=INACTIVE, need convert    ← ③ 需要转换
14:30:02  [ConversionService] addConversionTask taskId=task456    ← ④ 转换入队
...（TaskServer 日志中，按 task456 搜索）...
14:30:15  [ConvertWorker] Result of Conversion task [task456]: [1299]  ← 转换成功
14:30:15  [ConversionService] receiveTask importDone docId=doc123  ← 导入完成
14:30:15  [DocumentService] joinSession docId=doc123               ← 加入会话
14:30:16  [ConnectionManager] DOCUMENT_OPEN docId=doc123           ← 文档打开成功
```

### 方法二：按 taskId 定位转换问题

当问题明确在文档转换阶段（如 IMPORTING 超时、转换失败）时，使用 taskId 更精确：

```bash
# 在 TaskServer 日志中搜索 taskId（转换执行详情）
rg '<taskId>' $LOG_DIR/TaskServer_*/{combined,error,java}*.log* | sort

# 关注的关键事件：
# [Task Server] Receive Task : [<taskId> / <type>]     → 接收
# [LocalConvertServiceImp] Download ... done in Xms    → 下载完成
# [LocalConvertServiceImp] tid:<taskId> fileSize is X B → 文件大小
# [CLConvertor] ExitCode (code=X;errCode:Z;id=<taskId>) → CL 转换结果
# [ConvertWorker] Result of Conversion task [<taskId>]: [<code>] → 最终结果
```

### 方法三：按时间段扫描（无 docId/taskId 时）

```bash
# 1. 搜索该时间段内的文档打开错误
rg 'errorCode|error\.ejs|importError|importFailProcess' $LOG_DIR/NewDocServer_*/combined*.log* | rg '14:30'

# 2. 搜索该时间段内的转换失败
rg '\[ConvertWorker\] Result' $LOG_DIR/TaskServer_*/combined*.log* | rg '14:30' | rg -v '1299'

# 3. 搜索该时间段内的 WebSocket 错误
rg 'AUTH_OTHER_ERROR|SESSION_EXPIRE|REACH_MAX' $LOG_DIR/NewDocServer_*/combined*.log* | rg '14:30'

# 4. 也可使用分析脚本自动化（见"脚本辅助"章节）
```

---

## 请求全链路

```
① HTTP 入口（页面准入）
   浏览器 GET/POST /:repo/:docid/:action/content
   → setLanguageInfo → openPostHandler（driver-callback 参数缓存）
   → openDocHandler（三方 HMAC 校验）→ authorizeOnlyForViewEdit（JWT/Token 认证）
   → setCommonInfo → setGateKeeper → openDoc_ls19（页面渲染）
   校验链：
     a) validateRequest — repo/action/docId 合法性
     b) checkLicense — License 存在/有效/到期/模块开关
     c) checkMaxActiveEditingDocs — 全局连接数上限
     d) getDocumentEntryFromRepo — 拉取文档 meta/权限（三方/Box/本地）
     e) validateDocumentAction — 文件类型支持检查
     f) checkEditAndSetMode / checkViewAndSetMode — 读写权限校验
   → 成功：渲染 EJS 页面壳（docInfo 注入前端）
   → 失败：渲染 error.ejs + errorCode

② WebSocket 连接
   前端加载后建立 Socket.IO 连接
   → allowRequest — JWT 校验 + Origin 白名单
   → 根 namespace middleware — 全局/单用户连接数限制
   → DOC namespace middleware — clientId/token 校验
   → 重连判断（Redis participant 检查）
   → 连接成功

③ 文档打开（WebSocket 命令）
   → OPEN 命令（编辑）/ VIEW 命令（预览）
   → DocumentService.openDoc_ls19 / viewDoc_ls19
   → meta 状态机判断：
     INACTIVE → 需转换 → startConvert_ls19
     IMPORTING → 等待（3min 超时，最多重试 3 次）
     ACTIVE → 直接 joinSession
     ERROR → 返回持久化 errorCode
     CONFLICT → 版本冲突处理
   → 预转版复用检查（historyRlmSeq）

④ 文档转换（TaskServer 异步）
   → ConversionService.addConversionTask_ls19 → Redis/Bull 队列
   → ConvertWorker.execute_ls19 → ConvertFileService.downloadSource_ls19
   → LocalConvertSrvImp: validate → execute(CLConvertor/Aspose等) → postConvert(upload)
   → 转换结果通过 convertResponse 队列返回 DocsServer
   → ConversionService.receiveTask_ls19
     成功 → emit CONVERSION_IMPORT_DONE → importDone_ls19 → StoreDraft → joinSession → 广播 DOCUMENT_OPEN
     失败 → emit CONVERSION_IMPORT_ERROR → importError_ls19 → meta INACTIVE + errorCode → 广播错误

⑤ Draft 加载
   → 前端 GET /api/:repo/:docid/draft/:seq
   → DraftStorageService.getDraftContentByRefId_ls19
   → 返回 JSON 流 → 前端编辑器加载

⑥ OT 协同编辑
   → WebSocket MESSAGE 事件 → Redis Stream → OTMessageListener
   → OTDocument 变换 + seq 校验 + 持久化(MongoDB) + 广播
   → ApplyMsg 定时/session 关闭时将 OT 消息应用到 draft

⑦ 保存/发布
   → WebSocket PUBLISH 命令 → exportDoc_ls19
   → ExportDocWorker: applyMessages → convert(JSON→DOCX/XLSX等)
   → publishDoc_ls19: uploadNewVersion → resetMeta → saveRevision
   → PublishDocEvent 通知前端

⑧ 资源加载
   → GET Pictures/:resource → DraftStorageService → ContentService 流
   → WMF/EMF 懒转换（404 → resourceConvert → 409 importing → 转换完成后重新请求）
```

---

## 阶段定位：根据现象判断

| 现象 | 所在阶段 | 关键日志前缀 |
|------|---------|-------------|
| 浏览器显示错误页面（error.ejs） | ① HTTP 入口 | `[apigateway_api_docs]` |
| 打开文档后白屏/转圈不动 | ②③④ WebSocket/转换 | `[ConnectionManager]` `[DocumentService]` |
| 提示"文档正在转换中"但一直不结束 | ④ 转换超时/IMPORTING | `[importFailProcess]` `[ConversionService]` |
| 提示"无权编辑" | ①③ 权限检查 | `[apigateway_api_docs]` |
| 提示"License 过期/超限" | ① License 检查 | `[apigateway_api_docs]` |
| WebSocket 断开后无法重连 | ② WebSocket | `[ConnectionManager]` |
| 编辑时内容丢失/冲突 | ⑥ OT 协同 | `[OTDocument]` `[OTMessageListener]` |
| 前端提示 Reload | ⑥ OT seq gap | `[OTDocument]` |
| 保存失败 | ⑦ 保存/发布 | `[DocumentService] export/publish` |
| 导出 PDF 超时 | ⑦ 导出 | `[ExportDocWorker]` `[ConvertWorker]` |
| 图片显示不出来 | ⑧ 资源加载 | `[attachment]` |
| 图片显示为"正在转换" | ⑧ WMF/EMF 懒转换 | `[ConversionService] resourceConvert` |
| draft 加载失败 | ⑤ Draft | `[DraftStorageService]` `[openDraft]` |
| 文档版本冲突提示 | ③ CONFLICT | `[DocumentService]` |
| 提示"文件过大" | ①④ 大小检查 | `[DocumentService] CONTENT_TOOLARGE` |

---

## 日志搜索

```bash
LOG_DIR=/opt/zdocs/luoshu_log

# ═══════════════════════════════════════
# 通用：按 docId 串联全链路
# ═══════════════════════════════════════
rg '<docId>' $LOG_DIR/*/{combined,error}*.log* | sort

# ═══════════════════════════════════════
# ① HTTP 入口层
# ═══════════════════════════════════════
rg '\[apigateway_api_docs\]' $LOG_DIR/*/{combined,error}*.log*
rg 'errorCode|error\.ejs' $LOG_DIR/*/combined*.log*
rg 'License|license' $LOG_DIR/*/{combined,error}*.log*
rg 'NO_RIGHT_EDIT_FILE|EC_REPO_NOVIEWPERMISSION|FILE_NOT_FOUND' $LOG_DIR/*/combined*.log*
rg 'DOC_TYPE_NOT_SUPPORT|DOC_EDIT_NOT_SUPPORT|DOC_PREVIEW_NOT_SUPPORT' $LOG_DIR/*/combined*.log*
rg 'EXCEED_MAX_SESSION' $LOG_DIR/*/combined*.log*

# ═══════════════════════════════════════
# ② WebSocket 连接
# ═══════════════════════════════════════
rg '\[ConnectionManager\]' $LOG_DIR/*/{combined,error}*.log*
rg 'allowRequest|AUTH_OTHER_ERROR|SESSION_EXPIRE' $LOG_DIR/*/{combined,error}*.log*
rg 'REACH_MAX_CONNECTIONS|SINGLE_EDITOR_ONLY|DOCUMENT_EXCEED_CONCURRENT' $LOG_DIR/*/combined*.log*
rg 'dead user|dead client' $LOG_DIR/*/combined*.log*
rg 'disconnect|leave doc|Reload' $LOG_DIR/*/combined*.log*

# ═══════════════════════════════════════
# ③ 文档打开（DocumentService）
# ═══════════════════════════════════════
rg '\[DocumentService\]' $LOG_DIR/*/{combined,error}*.log*
rg 'DRAFT_VERSION_CONFLICT|NEW_DOC_INCOMPLETE|IMPORT_TASK_INCOMPLETE' $LOG_DIR/*/combined*.log*
rg 'CONTENT_TOOLARGE|DOC_ONLINEEDIT_LOCKED' $LOG_DIR/*/combined*.log*
rg 'startConvert|needConvert|preConvert' $LOG_DIR/*/combined*.log*
rg 'importFailProcess' $LOG_DIR/*/{combined,error}*.log*

# ═══════════════════════════════════════
# ④ 文档转换
# ═══════════════════════════════════════
rg '\[ConversionService\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[ConvertWorker\]' $LOG_DIR/*/{combined,error}*.log*
rg 'Result of Conversion task' $LOG_DIR/*/combined*.log*
rg '\[LocalConvertServiceImp\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[CLConvertor\]|\[ClConvertor\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[AConvertor\]' $LOG_DIR/*/{combined,error}*.log*
rg 'CONVERSION_IMPORT_DONE|CONVERSION_IMPORT_ERROR' $LOG_DIR/*/combined*.log*
rg 'importDone|importError' $LOG_DIR/*/combined*.log*
# Java 层
rg 'ERROR|Exception' $LOG_DIR/*/java*.log*
# 预转版
rg '\[ConversionService\].*previewPreconvert' $LOG_DIR/*/combined*.log*
rg 'queue is full|previewPreconvert' $LOG_DIR/*/combined*.log*

# ═══════════════════════════════════════
# ⑤ Draft 加载
# ═══════════════════════════════════════
rg '\[DraftStorageService\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[openDraft\]' $LOG_DIR/*/{combined,error}*.log*
rg 'Could not find draft|Could not get draft' $LOG_DIR/*/{combined,error}*.log*

# ═══════════════════════════════════════
# ⑥ OT 协同编辑
# ═══════════════════════════════════════
rg '\[OTDocument\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[OTMessageListener\]' $LOG_DIR/*/{combined,error}*.log*
rg '\[OTSessionConsumer\]' $LOG_DIR/*/{combined,error}*.log*
rg 'duplicated message|seq gap|Reload|OtMsgReload' $LOG_DIR/*/combined*.log*
rg 'Invalid sequence|Failed to open OT' $LOG_DIR/*/{combined,error}*.log*
rg '\[DocumentSessionUtil\]' $LOG_DIR/*/combined*.log*
rg 'recreate session' $LOG_DIR/*/combined*.log*
rg 'APPLYMESSAGE_UNKNOWN|applyMessages.*error' $LOG_DIR/*/{combined,error}*.log*

# ═══════════════════════════════════════
# ⑦ 保存/发布/导出
# ═══════════════════════════════════════
rg '\[DocumentService\].*export|publish' $LOG_DIR/*/{combined,error}*.log*
rg '\[ExportDocWorker\]' $LOG_DIR/*/{combined,error}*.log*
rg 'PUBLISH_REMOTE_ERROR|REPO_UNKNOWN_EXCEPTION' $LOG_DIR/*/{combined,error}*.log*
rg 'duplicate_export|publish lock' $LOG_DIR/*/combined*.log*
rg 'SAVEF_|saveFailCode' $LOG_DIR/*/combined*.log*
rg 'uploadNewVersion|Could not upload' $LOG_DIR/*/{combined,error}*.log*

# ═══════════════════════════════════════
# ⑧ 资源加载
# ═══════════════════════════════════════
rg '\[attachment\]' $LOG_DIR/*/{combined,error}*.log*
rg 'getResource.*not find|resourceConvert|resourceImportDone|resourceImportError' $LOG_DIR/*/combined*.log*
```

---

## DocsErrorCode 速查（文档打开/编辑全阶段）

> 枚举定义在 `share/types/commonTypes.ts`（外部共享包），值为字符串常量。

### ① HTTP 入口层

| 错误码 | 触发场景 |
|--------|----------|
| `MALFORMED` | 非法 repo/action/docId 含冒号 |
| `UNSUPPORTED_BROWSER` | 浏览器黑名单 |
| `DOC_ID_NOT_ALLOWED_COLON` | docId 含 `:` 字符 |
| `SUB_DOC_EDIT_NOT_SUPPORT` | 编辑模式 + 附件子文档 |
| `FILE_NOT_FOUND` | 无法获取文档 Entry（repo 404） |
| `DOC_PREVIEW_NOT_SUPPORT` | 预览不支持该文件格式 |
| `DOC_EDIT_NOT_SUPPORT` | 编辑不支持该文件格式 |
| `DOC_TYPE_NOT_SUPPORT` | 文件类型不支持（无 AppService） |
| `NO_RIGHT_EDIT_FILE` | 无编辑权限 |
| `EC_REPO_NOVIEWPERMISSION` | 无预览权限 |
| `CONTENT_CAD_MOBILE_TOOLARGE` | CAD 文件移动端过大 |
| `EC_DOCUMENT_EXCEED_MAX_SESSION_ERROR` | 全局活跃编辑文档数超限（默认 1000） |
| `NOT_ENABLE_EDIT` | License v5 编辑能力未开启 |
| `NOT_ENABLE_PREVIEW` | License v5 预览能力未开启 |
| `LICENSE_EXCEED_USERS` | License 用户数超限 |
| `LICENSE_EXPIRE` | License 已过期 |
| `LICENSE_NOT_AVAIL` | License 不可用 |
| `PORTAL_APP_EXPIRED` | SaaS 应用过期 |
| `PORTAL_APP_NOT_FOUND` | SaaS 应用不存在 |
| `DRIVER_CB_*` 系列 | Driver-callback 集成错误 |

### ② WebSocket 连接层

| 错误码 | 触发场景 |
|--------|----------|
| `AUTH_OTHER_ERROR` | token/clientId 为空、middleware 异常、Origin 不在白名单 |
| `SESSION_EXPIRE` | JWT 过期 |
| `REACH_MAX_CONNECTIONS_PERUSER` | 单用户 Socket 连接数超限（默认 100） |
| `EC_DOCUMENT_EXCEED_MAX_SESSION_ERROR` | 全局 Socket 连接数超限（默认 1500） |
| `REACH_MAX_CONNECTIONS_PERDOC` | 单文档连接数超限（默认 200） |
| `SINGLE_EDITOR_ONLY` | 单编辑者模式（License v2 m=0 或 PDF），已有其他编辑者 |
| `DOCUMENT_EXCEED_CONCURRENT` | PDF 多人并发编辑 |
| `DOC_ONLINEEDIT_LOCKED` | 远端 onlineEdit 锁定（WARNING 级别，不阻断） |

### ③ 文档打开/转换层

| 错误码 | 触发场景 |
|--------|----------|
| `CONTENT_TOOLARGE` | 文档超大（打开/转换时检查） |
| `DRAFT_VERSION_CONFLICT` | 远端新版本 vs 本地未发布的 dirty draft |
| `NEW_DOC_INCOMPLETE` | 新建文档 draft 未就绪（meta 状态 NEW_INACTIVE） |
| `DRAFT_NOT_CREATED` | 新建文档模板失败 |
| `IMPORT_TASK_INCOMPLETE` | IMPORTING 超时重试耗尽（3min×3 次） |
| `CONVERSION_NOT_START` | 转换任务入队失败 |
| `CONVERSION_MAJORUPGRADE_ERR` | 大版本升级有未 apply 的消息 |
| `PREVIEW_PRECONVERT_QUEUE_FULL` | 预转队列满 |

### ④ 转换结果错误码

| DocsErrorCode | 来源 ConvertErrCode | 含义 |
|---------------|---------------------|------|
| `CONVERSION_NOERR` | 0, 200, 501, 906 | 成功（含警告） |
| `CONVERSION_UNKNOWN` | 520, 525, 526 等 + 1215-1299 区间归一化 | 未知转换错误 |
| `CONVERSION_TIMEOUT` | 495, 521, 1006 | 转换超时 |
| `CONVERSION_FOTMAT_ERR` | 415, 529 | MIME 类型/文件格式错误 |
| `CONVERSION_DOWNLOAD_ERR` | 1001 | 源文件下载失败 |
| `CONVERSION_UPLOAD_ERR` | 1002 | 转换结果上传失败 |
| `CONVERSION_INVALID_PASSWORD` | 491 | 加密文档密码错误 |
| `CONVERSION_UNSUPPORTED_ENCRYPTION` | 492 | 不支持的加密方式（ODF） |
| `CONVERSION_SERVER_BUSY` | 493, 1011 | 转换引擎繁忙 |
| `CONVERSION_SOFFICE_ERR` | 496, 497 | Symphony 引擎错误 |
| `CONVERSION_CL_ERR` | 522, 523, 524, 527, 528 | CL/OOXML 转换错误 |
| `CONVERSION_CAD_ERR` | 1005 | CAD 转换错误 |
| `CONVERSION_EXTRACT_TOO_MANY` | 1013 | 解压条目过多 |
| `FETCH_REMOTE_FILE_DECRYPT_ERROR` | 1004 | 远端加密文件解密失败 |
| `CONTENT_TOOLARGE` | 413 | 文件过大 |
| `CONTENT_LIMIT` | 901 (530-539) | 内容超限（页数/行列/单元格等） |
| `DOWNLOAD_FILE_TOO_SMALL` | 1014 | 下载文件过小（OOXML < 1KB） |

> 1215-1299 范围的错误码会被 `ErrorUtil` 统一归一为 `CONVERSION_UNKNOWN`。
> `906 CONTENT_UNSUPPORTED` 被映射为 `CONVERSION_NOERR`（警告性，不代表失败）。

### ⑥ OT 协同/ApplyMsg

| 错误码/信号 | 触发场景 |
|------------|----------|
| `APPLYMESSAGE_NOERR` | ApplyMsg 成功 |
| `APPLYMESSAGE_UNKNOWN` | ApplyMsg 未知错误 |
| `APPLYMESSAGE_NO_NEW_MESSAGE` | 无新消息需应用 |
| `APPLYMESSAGE_DOCTYPE_NOT_SUPPORTED` | 文档类型不支持 apply |
| `OtMsgReload`（ActionEvent） | OT server seq 与 client seq 差距 > 100 |
| `Reload`（ActionEvent） | 长断开重连需重载模型 |
| `toBeExpired`（ActionEvent） | JWT 即将过期（< 30s） |

### ⑦ 保存/发布

| 错误码 | 触发场景 |
|--------|----------|
| `PUBLISH_REMOTE_ERROR` | 回写第三方失败 / 重复导出 |
| `PUBLISH_REMOTE_UNKNOWN_ERROR` | 回写未知错误 |
| `PUBLISH_REMOTE_FILE_ENCRYPT_ERROR` | 加密原文件失败 |
| `REPO_UNKNOWN_EXCEPTION` | 仓库上传异常 |
| `SAVEF_{n}` | 保存失败计数（meta.errorCode，非标准 DocsErrorCode） |

### ⑧ 资源/Draft/权限

| 错误码 | 触发场景 |
|--------|----------|
| `NO_RIGHT_READ_FILE` | Draft 拉取无读权限 |
| `NO_RIGHT_REVISION_DOC` | 无版本管理权限 |
| `META_REQUEST_FAIL` | 三方 meta API 请求失败 |
| `META_BAD_INFO` | 三方 meta 信息异常 |

---

## 文档打开完整流程排查

### Meta 状态机

| MetaStatus | 行为 | 错误码 |
|------------|------|--------|
| `INACTIVE` | 触发 startConvert → 任务入队 | CONVERSION_NOT_START（入队失败） |
| `IMPORTING` | 等待中；3min 后 importFailProcess | IMPORT_TASK_INCOMPLETE（重试 3 次后） |
| `ACTIVE` | 直接 joinSession → draft 就绪 | — |
| `ERROR` | 返回 meta.errorCode 给前端 | 持久化的错误码 |
| `CONFLICT` | 版本冲突处理 | DRAFT_VERSION_CONFLICT |
| `NEW_INACTIVE` | 新建文档 draft 未完成 | NEW_DOC_INCOMPLETE |

### IMPORTING 超时策略

```
IMPORT_EXPIRE_TIME = 3 × 60 × 1000        // 3min 触发 importFailProcess
MAX_IMPORT_EXPIRE_TIME = 60 × 60 × 1000   // 60min 强制 reset INACTIVE
IMPORT_RETRY_MAX_NUM = 3                   // 超过 → IMPORT_TASK_INCOMPLETE
```

### 预览 vs 编辑差异

| 维度 | 预览 (view) | 编辑 (edit) |
|------|-------------|-------------|
| Meta 类型 | `preview` / `snapshot` | `active` |
| Draft | 与 active 共用（snapshot 除外） | 独占 active draft |
| WebSocket | `CommandType.VIEW` | `CommandType.OPEN` |
| 大小限制 | `*_preview` 配置项（通常更大） | 普通 `maxfilesize` |
| 并发 | 预览 socket 完成后可断开 | 多人协同（Word/Excel）；PDF 强制单人 |
| 预转版 | 优先 simpleCopy | — |

### 目标 draft 格式

| 源文件类型 | Draft 格式 | 转换引擎 |
|-----------|-----------|---------|
| DOCX/DOC/WPS 等 | JSON | CL(OOXML 二进制) / Aspose |
| XLSX/XLS/ET 等 | JSON | CL / Aspose |
| PPTX/PPT/DPS 等 | PRES_HTML_DRAFT | CL → HTML |
| PDF | PDF_JSON_DRAFT | PDF 解析器 |
| OFD（snapshot） | PDF | OFD2PDF(Java) |
| CAD | 独立 CADCONVERT 任务 | — |
| 音视频 | LONG_TIME_CONVERT → MP4/MP3 | FFmpeg 等 |

---

## WebSocket 连接排查

### 连接生命周期

```
HTTP Upgrade → allowRequest(JWT+Origin) → 根 namespace(连接数限制)
→ DOC namespace(clientId+token) → 连接成功
→ OPEN/VIEW 命令 → 文档打开逻辑
→ HeartBeat 事件维持（更新 beatTime）
→ disconnect → 60s 等待重连 → 未重连则 leaveDoc
```

### 认证错误

| 场景 | 表现 | 日志 |
|------|------|------|
| JWT 过期 | HTTP 403，连接失败 | `allowRequest callback(10, false)` |
| JWT 即将过期 | WebSocket 事件 `toBeExpired` | `testJWTToken → PASS_DUE` |
| Origin 不在白名单 | HTTP 403 | `allowRequest` 失败 |
| 缺 clientId/token | AUTH_OTHER_ERROR 事件 → 200ms 后断开 | `[ConnectionManager] auth error` |

### 心跳与超时

| 机制 | 阈值 | 行为 |
|------|------|------|
| 应用层 HeartBeat | 客户端 30s 一次 | 更新 Redis `beatTime`，回包 OT servers 列表 |
| Dead Client 检测 | `now - beatTime > 180s`（6×30s） | `removeDeadClient` |
| Dead OT Server | `serverBeatTime > 120s`（4×30s） | 移除 OT server 条目 |
| Disconnect → Leave | 60s | 未重连则触发 leaveDoc |
| WOPI HeartBeat | 20min | 刷新文档 lock |

### 重连机制

1. 客户端重连带 `cId`（clientId），URL 含 docId/repoId/mode
2. `isUserReconnect`：同 clientId + 不同 connectionId → 短重连（joinDoc 继续编辑）
3. Redis participant 已清理 → 长断开 → 发 `Reload` 事件，前端刷新模型
4. OT failover：进程重启 → `startFailOver_ls19` 恢复文档 OT 监听

---

## OT 协同编辑排查

### 消息流

```
Client MESSAGE → ConnectionManager → DocumentSessionUtil.processMessage → Redis Stream
→ [OTGroup] OTMessageListener → OTDocument 变换 + MongoDB 持久化 + 广播
→ [DispatchGroup] DispatchMessageListener → 原始消息广播给其他客户端
```

### 常见 OT 错误

| 场景 | 表现 | 日志关键字 | 处理 |
|------|------|-----------|------|
| 重复消息 | 丢弃 | `duplicated message` | 客户端重发，OT 去重 |
| Sheet seq 缺口 | 仅 warn | `message seq gap` | 不阻断，可能丢消息 |
| Server seq 差距 > 100 | 强制 Reload | `seq gap is more than limit` | 客户端重载模型 |
| Client seq > Server seq | 同步 DB seq | `syncServerSeq` | 以 DB 为准 |
| OT 变换冲突 | 加入 conflictClients | `Result.Conflict` | 等待 ResolveConflict 消息 |
| Redis Stream seq 乱序 | 关闭 listener | `Invalid sequence` | 重启处理 pending |
| MongoDB 写消息失败 | 仅 error 日志 | `can not save messages` | 存在不一致风险 |
| Session 不存在仍发消息 | 丢弃 | `session doesn't exist` | warn |
| dataHidden 文档 SyncState | 返回 error 事件 | `canSyncState=false` | 前端 reload |

### ApplyMsg 错误

| ApplyMsg 错误码 | 含义 | 影响 |
|-----------------|------|------|
| `APPLYMESSAGE_NOERR` | 成功 | — |
| `APPLYMESSAGE_NO_NEW_MESSAGE` | 无新消息 | 非错误，ExportDoc 继续 |
| `APPLYMESSAGE_DOCTYPE_NOT_SUPPORTED` | 类型不支持 | 保存失败 |
| `APPLYMESSAGE_UNKNOWN` | 未知错误 | 保存失败，计入 SAVEF 计数 |

---

## 保存/发布排查

### 保存流程

```
WebSocket PUBLISH → exportDoc_ls19
  → 并发锁检查（Redis ZSet，5min TTL）
  → 密码/脏数据检查
  → addExportDocTask_ls19 → Bull 队列
  → ExportDocWorker: applyMessages → convert(JSON→原始格式)
  → exportDocResponse → exportDone_ls19
  → publishDoc_ls19:
    → getContent → uploadNewVersion(回写第三方)
    → resetMeta(isDraftDirty=false) → saveRevision
  → PublishDocEvent 通知前端
```

### 保存失败场景

| 阶段 | 错误码 | 场景 | 日志关键字 |
|------|--------|------|-----------|
| 并发导出 | `PUBLISH_REMOTE_ERROR` + `duplicate_export` | 同一文档重复发布 | `still being exported` |
| ApplyMsg 失败 | `APPLYMESSAGE_UNKNOWN` | draft 应用 OT 消息异常 | `[ExportDocWorker] applyMessages error` |
| 转换失败 | 对应 CONVERSION_* | JSON→DOCX/XLSX 等反向转换失败 | `[ConvertWorker] Result of Conversion task` |
| 加密失败 | `PUBLISH_REMOTE_FILE_ENCRYPT_ERROR` | 原文件加密失败 | `encrypt_fail` |
| 回写失败 | `PUBLISH_REMOTE_ERROR` / `REPO_UNKNOWN_EXCEPTION` | uploadNewVersion 到第三方失败 | `Could not upload new version` |
| 发布锁获取失败 | — | `can not get publish lock` | `publish lock` |
| 保存失败计数 | `SAVEF_{n}` | 累计失败次数 | `saveFailCode` |

> 保存失败次数达上限（默认 10 次）→ 触发 draft 备份到 Redis。
> 自动保存（session 断开/过期）失败仅 warn 日志，不通知用户。

### 版本冲突处理

```
远端新版本(remoteLastModified > publishedAt) + 本地有未发布修改(isDraftDirty)
  → DRAFT_VERSION_CONFLICT
  → 用户选择：
    1. 编辑新版本 → autoPublish(isHandleConflict=true) → 重新转换新版本
    2. 放弃本地修改 → 重新打开
```

---

## 预转版排查

### 触发时机

| 时机 | 入口 | 说明 |
|------|------|------|
| 业务系统 Webhook | `POST webhookIncomingMsgs`（eventType=PreviewConvert） | 最多 20 条/批 |
| 首次预览 | `startConvert_ls19` + preview meta | meta INACTIVE 时触发 |
| 有 historyRlmSeq | `preConvert()` → `simpleCopyDraftBySeq` | 复用已有 draft，免转换 |

### 预转版过滤条件

- 扩展名 ∈ `previewPreconvert_SupportedExt`（配置）
- 文件大小 ≥ `previewPreconvert_minSize`（按 docType，单位 KB）
- 已有 editMeta → 跳过（编辑过的文档不再预转）

### 预转版错误

| 场景 | 表现 | 日志关键字 |
|------|------|-----------|
| 队列满 | `PREVIEW_PRECONVERT_QUEUE_FULL` | `queue is full` |
| 重复任务 | 静默跳过 | `duplicate` |
| 转换失败 | meta 恢复 INACTIVE + errorCode | `importError` |
| 被 convert 暂停 | 队列 pause | `active count over threshold` / `Queue is paused` |

> 预转版并行度默认 2，不重试（retry=0）；convert 活跃数 ≥ 4 时暂停预转版队列。

---

## Draft 加载排查

### 常见 Draft 错误

| 场景 | 表现 | 日志关键字 |
|------|------|-----------|
| draft DB 记录不存在 | throw Error | `Could not get draft ... in db` |
| contentRefId 不存在 | 返回 null | `Could not find draft content with ref id` |
| seq 无对应 content | refId=null | `getDraftContentRefId refId null` |
| JSON 解析失败 | throw | `JSON.parse` / `JSONStream error` |
| 存储流错误 | reject | `getContent stream error` |
| 附件 404 | throw 404 | `error.name='404'` |
| 更新 draft 锁竞争 | throw | `can not get lock to update draft` |
| contentJson 缺失(PPT) | null | `Could not find draft contentJson` |

### Draft 版本清理

- 版本上限：`maxDraftVersion` = 10
- 均匀保留：`deleteContentsForEquably_ls19` 按理论列表保留关键点
- PDF 特殊：多 seq 共享同一 PDF content，删除用引用计数
- HouseKeeping：`dropMetaAndDraft_ls19` 清理过期 rlm 的 meta/draft/content/message

---

## 资源加载排查

### 图片/附件

| HTTP 状态 | 场景 | 日志关键字 |
|-----------|------|-----------|
| 200 | 正常返回 + Cache-Control | — |
| 404 | 资源不存在 / entry null | `Did not find the resource` |
| 409 | WMF/EMF 正在转换（`importing` query + Redis lock） | `resourceConvert` |
| 500 | 其他异常 | — |
| 400 | 上传 mime/ext 不允许 | — |

### WMF/EMF 懒转换

```
GET Pictures/xxx.wmf → 404
→ checkSourceAndConvert → resourceConvert_ls19(WMF→PNG) → 409 importing
→ TaskServer 转换完成 → resourceImportDone_ls19
→ 前端重新请求 → 200
```

> 转换失败仅 unlock Redis lock（`resourceImportError_ls19` 不推送前端），前端下次请求仍 404。

### 字体

- 启动时：`FontService.checkAndCacheFontFile_ls19`（仅 INSTANCE_ID=0 执行）
- 系统字体：`/usr/share/fonts`
- 自定义字体：Admin 上传 → MongoDB `fontinfos` → 本地缓存
- 缺失：Java Aspose 层 fallback（`FontSettings.getDefaultInstance`），不阻断文档打开
- 失败记录：`FontService.failedFileName[]`

---

## 超时配置汇总

| 组件 | 配置项 | 默认值 |
|------|--------|--------|
| Convert 任务超时 | `docscommon.task.config.default.timeout` | 180,000ms (3min) |
| Convert 重试次数 | `docscommon.task.config.default.retry` | 3 |
| ExportDoc 任务超时 | `docscommon.task.config.exportDoc.timeout` | 300,000ms (5min) |
| ApplyMsg 超时 | `docscommon.task.config.applyMsg.timeout` | 300,000ms (5min) |
| ResourceConvert 超时 | `docscommon.task.config.resourceConvert.timeout` | 180,000ms (3min) |
| 预转版重试 | `docscommon.task.config.previewPreconvert.retry` | 0（不重试） |
| 预转版队列上限 | `docscommon.task.config.previewPreconvert.maxQueueSize` | 100,000 |
| Convert 暂停阈值 | `docscommon.task.config.convert.threshold` | 4 |
| IMPORTING 超时 | 硬编码 | 3min（每次） |
| IMPORTING 最大超时 | 硬编码 | 60min |
| IMPORTING 最大重试 | 硬编码 | 3 次 |
| WebSocket disconnect→leave | 硬编码 | 60s |
| Dead Client 检测 | `6 × CLIENT_HB_INTERVAL` | 约 180s |
| Dead OT Server | `4 × CLIENT_HB_INTERVAL` | 约 120s |
| 导出并发锁 TTL | `EXPORT_DOC_LIFETIME` | 5min |
| CL 转换超时 | `task.timeout` / `MaxConvertTime` | 300,000ms (5min) |
| Java 内嵌超时 | `taskserver.convert.local.java.taskTimeout` | 300,000ms (5min) |
| Draft 版本上限 | `maxDraftVersion` | 10 |

---

## 连接数配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `maxActiveEditingDocs` | 1000 | 全局活跃编辑文档数上限 |
| `maxSocketConnections` | 1500 | 全局 Socket 连接数上限 |
| `maxSocketConnPerUser` | 100 | 单用户 Socket 连接数上限 |
| `maxConnectionsPerDoc` | 200 | 单文档连接数上限（PDF/License 可降为 1） |
| Convert maxParallels | 16 | 转换 Worker 并发 |
| PreviewPreconvert maxParallels | 2 | 预转 Worker 并发 |
| ExportDoc maxParallels | 16 | 导出 Worker 并发 |

---

## 文件大小限制

| 文档类型 | 编辑限制 (KB) | 预览限制 (KB) | 配置前缀 |
|---------|--------------|--------------|----------|
| Word (text) | 307,200 (300MB) | 同上或更大 | `maxfilesize.text` |
| Sheet (sheet) | 307,200 (300MB) | 同上 | `maxfilesize.sheet` |
| PPT (pres) | 102,400 (100MB) | 同上 | `maxfilesize.pres` |
| PDF (pdf) | 204,800 (200MB) | 307,200 (300MB) | `maxfilesize.pdf` / `pdf_preview` |

> 预览模式使用 `*_preview` 配置项，通常限制更宽松。

---

## 常见复合场景诊断

### 场景1：文档打开白屏/一直转圈

```
排查步骤：
1. 搜索 docId 的完整日志 → 确认走到哪一步
2. 检查 meta 状态：
   - 若无 meta 日志 → ① HTTP 入口层错误，看 error.ejs
   - 若 IMPORTING → ④ 转换中，检查 TaskServer 日志
   - 若 ERROR → 看 meta.errorCode
3. WebSocket 是否成功建立：搜索 [ConnectionManager] 中该用户的连接日志
4. OPEN/VIEW 命令是否发出：搜索 [DocumentService] openDoc 日志
5. 转换任务是否入队：搜索 [ConversionService] addConversionTask
6. 转换是否完成：搜索 [ConvertWorker] Result of Conversion task
7. importDone/importError 是否触发
8. draft GET 请求是否成功
```

### 场景2：文档 IMPORTING 超时

```
排查步骤：
1. rg 'importFailProcess' → 确认超时触发
2. 确认转换任务是否在 TaskServer 执行：搜索 taskId
3. 转换任务是否挂起：检查 Bull 队列状态（active/waiting）
4. TaskServer 是否存活：检查健康检查日志
5. 是否队列积压：rg 'threshold|paused'
6. 重试次数：IMPORT_RETRY_MAX_NUM=3，超过 → IMPORT_TASK_INCOMPLETE
7. 大文件超时：检查 CL/Aspose 转换是否超时（5min 默认）
```

### 场景3：WebSocket 频繁断线重连

```
排查步骤：
1. rg '[ConnectionManager].*disconnect' → 断线频率
2. rg 'dead user|dead client' → 心跳超时
3. rg 'Reload' → 是否触发了强制重载
4. 检查网络环境（代理/负载均衡 idle timeout）
5. 检查 Socket.IO Redis adapter 连接状态
6. 确认前端是否有 error 事件
```

### 场景4：OT 协同编辑内容丢失

```
排查步骤：
1. rg '[OTDocument].*<docId>' → OT 处理日志
2. 查看是否有 'duplicated message' → 消息重复
3. 查看是否有 'seq gap' → 消息缺口
4. 查看是否有 'Reload' → 强制重载（seq 差距 > 100）
5. 查看是否有 'Conflict' → OT 变换冲突
6. 检查 MongoDB docmessages 集合是否有断层
7. 检查 Redis Stream 消费状态
```

### 场景5：保存/导出失败

```
排查步骤：
1. rg '[DocumentService].*export|publish.*<docId>' → 保存流程日志
2. rg '[ExportDocWorker].*<docId>' → 导出 Worker 日志
3. 检查 ApplyMsg 是否成功：rg 'applyMessages.*error'
4. 检查转换是否成功：rg 'Result of Conversion task'
5. 检查回写是否成功：rg 'uploadNewVersion|Could not upload'
6. 检查并发冲突：rg 'duplicate_export|still being exported'
7. 检查保存失败计数：rg 'SAVEF_'
```

### 场景6：图片/资源加载失败

```
排查步骤：
1. rg '[attachment].*<docId>' → 资源请求日志
2. rg 'getResource.*not find' → 资源不存在
3. 检查是否是 WMF/EMF：rg 'resourceConvert' → 懒转换触发
4. 检查懒转换是否完成：rg 'resourceImportDone|resourceImportError'
5. 检查 draft 中 resources composite 是否包含该资源
6. 检查存储后端（MongoDB/GridFS）连接状态
```

### 场景7：PDF 预览失败

```
排查步骤：
1. 确认是否走 snapshot（PDF → PDF_JSON_DRAFT）
2. 检查转换日志：rg 'PDF.*<docId>'
3. PDF 加密：检查是否有 CONVERSION_INVALID_PASSWORD
4. PDF 过大：检查 CONTENT_TOOLARGE（预览限制通常 200-300MB）
5. PDF 内容解析失败：检查 java*.log 中的 PDF 解析异常
6. PDF 并发限制：DOCUMENT_EXCEED_CONCURRENT / SINGLE_EDITOR_ONLY
```

---

## 任务判定规则

| 判定依据 | 成功 | 失败 |
|---------|------|------|
| `Result of Conversion task [<tid>]: [<code>]` | code = 1299 | code ≠ 1299 |
| `Done the execution ... with code [<code>]` | code = 200 | code ≠ 200 |
| `[PDFMergeConvertor] ... is failed` | — | 始终失败 |
| `[ConversionService] ... is failed for error [<code>]` | — | 始终失败 |

> CLConvertor ExitCode `code=112; errCode:412` 可能伴随 `parser error`，但若最终 Result code 为 1299 则任务**成功**，忽略中间过程警告。

---

## 输出报告结构

1. **错误阶段**：①HTTP入口 / ②WebSocket / ③文档打开 / ④转换 / ⑤Draft加载 / ⑥OT协同 / ⑦保存发布 / ⑧资源加载
2. **错误码**：DocsErrorCode + ConvertErrCode（如涉及转换层）+ ApplyMsg 错误码（如涉及保存）
3. **关键日志**：带时间戳的原始日志片段，标注日志来源节点（DocsServer / TaskServer）
4. **Meta 状态**：当前 meta 状态和 errorCode
5. **根因**：结合链路知识分析具体原因
6. **修复建议**：配置调整 / 文件问题 / 基础设施问题 / 前端操作指引

---

## 注意事项

- 文档打开涉及 DocsServer（HTTP+WebSocket）和 TaskServer（转换任务）两个服务，日志分散在不同节点
- 同一文档的预览 meta 和编辑 meta 独立管理，排查时注意区分 `preview` vs `active` meta 类型
- IMPORTING 状态下新来的用户会被加入等待队列，不会触发新的转换任务
- `CONTENT_UNSUPPORTED`(906) 是警告而非错误，转换可能仍成功（映射为 `CONVERSION_NOERR`）
- 预转版使用 `fakePreconvId` 作为用户标识，日志中可据此区分预转版 vs 真实用户请求
- OT 消息持久化到 MongoDB 是异步的，写失败仅记日志不阻断广播
- 保存失败计数 `SAVEF_{n}` 存在 meta.errorCode 中，达上限触发 draft 备份
- 多节点下 WebSocket 通过 Redis pub/sub 跨节点广播，Room 命名：`/repo/{repoId}/doc/{docId}`
- ErrorUtil 会将 1215-1299 范围的错误码统一归一为 `CONVERSION_UNKNOWN`
- PDF/Markdown 文件强制单人编辑模式
- Draft 最多保留 10 个版本，超过时均匀清理旧版本

## 脚本辅助

```
docs/skills/scripts/analyze_task_failure.py
```

Python 3.6+，无额外依赖。分析日志时请直接通过 run_script 工具调用此脚本。

### 执行方式

```bash
# 按 docId 分析
python3 docs/skills/scripts/analyze_task_failure.py --logDir <LOG_DIR> --docId <docId>

# 按 taskId 分析
python3 docs/skills/scripts/analyze_task_failure.py --logDir <LOG_DIR> --taskId <taskId>

# 按时间段分析所有失败任务
python3 docs/skills/scripts/analyze_task_failure.py --logDir <LOG_DIR> --timeFrom "2026-05-22 14:00:00" --timeTo "2026-05-22 15:00:00"

# 同时输出成功任务
python3 docs/skills/scripts/analyze_task_failure.py --logDir <LOG_DIR> --docId <docId> --all
```

错误码参考：`docs/skills/references/error-codes.md`
