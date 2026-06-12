# 文档打开报错场景速查

> 诊断时先确定报错属于哪个场景，再按日志特征搜索验证。

## 通用日志搜索规范（每次诊断必须遵守）

1. **Node 日志**：搜索 `combined-*.log` + `error-*.log` + 所有轮转文件（`.log.1` `.log.2` 等），覆盖全部 NewDocServer / TaskServer 节点
2. **Java 日志**：搜索**所有** `java-systemOut*.log`（`.0.log` `.1.log` ... `.19.log` 及 `.0.log.1`），覆盖全部 TaskServer 节点
3. **时区换算**：Java 日志 UTC+0，Node 日志 UTC+8。例如 Node 端 `10:50` → Java 端 `02:50 AM`。**不要因 Java 时间"太早"而忽略**
4. **搜索轮次**：至少 3 轮——① taskId → ② SEVERE/Exception/Error + 时间窗口 → ③ 功能关键词（如 `Grpsp2pngConverter|Unknown image format`）
5. **截断检测**：`grep_log` 返回 `total: 30, limit: 30` 表示结果被截断，必须缩小范围重搜
6. **因果判断**：Node 端的 `ENOENT` / `file not exist` 可能只是**表象**，需反查 Java 端确认文件是否本应由 Java 子管线生成

## 场景索引

| 编号 | 报错类型 | 前端表现 | 错误码 |
|-----|---------|---------|-------|
| S1 | 文件格式不支持/损坏 | "转换服务不可用" | 1214 |
| S1a | MIME类型校验失败/扩展名不匹配 | "无法打开文档" | 1203(detail.id=415) |
| S2 | 源文件下载异常 | "转换服务不可用" | 1214,1001,1014 |
| S3 | 转换引擎OOM | "转换服务不可用" | 1214 |
| S4 | 转换超时 | 一直转圈 | 495,521,1006 |
| S5 | 加密文档 | 提示输入密码 | 491,492,1004 |
| S6 | 文件过大/内容超限 | "文件过大" | 413,901,530-539 |
| S7 | 权限/认证错误 | "无权访问" | HTTP错误码 |
| S8 | WebSocket连接失败 | 白屏/断线 | AUTH_OTHER_ERROR |
| S9 | License异常 | "License过期" | LICENSE_* |
| S10 | 文字乱码/字体缺失 | 文档内容显示为乱码或方块 | 无错误码(文档可打开) |
| S11 | SmartArt/组合图形图片缺失 | 预览时图片404/转PDF失败 | modelOpUnSupport |

---

## S1: 文件格式不支持/损坏

**日志关键字**：`[ConvertWorker] Catch Exception` / `UnsupportedFileFormatException` / `PptxReadException` / `[CLConvertor] stderr: Document is empty`

**典型子场景**：

| 异常 | 引擎 | 含义 |
|------|------|------|
| `UnsupportedFileFormatException: Unsupported file format: Unknown` | Aspose.Words / Aspose.Cells | 文件头魔数不匹配任何已知格式，常见原因：文件损坏/下载不完整/扩展名与实际格式不一致（如 `.docx` 实际是 HTML 或纯文本） |
| `PptxReadException` | Aspose.Slides | PPT/PPTX 文件结构损坏或不完整 |
| `Document is empty` + `ExitCode code=112` | CLConvertor(libxml2) | XML 解析器判定文件内容为空（0 字节 / 只有 XML 头无正文 / 下载截断） |
| `CellsException: Unknown image format` | Aspose.Cells (Grpsp2pngConverter) | xlsx 内嵌 SmartArt 图形中的图片格式不识别（见 S11 详情） |

**诊断**：搜索taskId找 `Catch Exception` 或 `CLConvertor stderr` → 看Java异常确认格式类型 → 检查fileSize → 如果 `UnsupportedFileFormatException`，需进一步确认文件实际格式（`xxd 文件 | head` 看魔数）

**用户验证**：获取下载日志 → 小文件用文本编辑器看内容 → WPS/Office打开验证 → 检查扩展名与格式是否一致 → `xxd 文件 | head` 看首字节（PK=ZIP/OOXML, D0CF=OLE/旧版Office, 3C3F=XML/HTML）

---

## S1a: MIME类型校验失败/扩展名不匹配

**错误码**：1203 → `CONVERSION_FOTMAT_ERR`（ConvertErrCode 415/529）

**日志关键字**：`convert import error` + `error=1203` / `"id":415,"description":"Conversion - Invalid File Mime Type"` / `correctSourceMIMEType:""` / `Document is empty` + `exited with non-zero code: 112`

**诊断**：搜索docId找`convert import error` → 检查detail的id字段(415=MIME不匹配,529=损坏) → 检查correctSourceMIMEType是否为空 → 同时间段大量相同错误可能是存储/下载批量异常

**用户验证**：下载原始文件用WPS/Office打开 → `xxd 文件 | head`检查首字节是否为PK(ZIP魔数) → 核对扩展名与实际格式 → 批量问题排查上传/存储/下载接口

---

## S2: 源文件下载异常

**日志关键字**：`failed to returned as source stream` / `fileSize is <极小值>` / `downloadFile size is <N>B, less than`

**诊断**：搜索Download+taskId → 检查fileSize → 大小0或极小说明源文件问题或下载接口返回错误内容

**用户验证**：curl调用下载接口 → 对比文件大小 → 小文件用文本编辑器看内容

---

## S3: 转换引擎OOM

**日志关键字**：`OutOfMemoryError: Java heap space` / `GC overhead limit exceeded`

**诊断**：搜索OutOfMemoryError+时间窗口 → 检查fileSize(通常>50MB) → 同时段大量OOM说明系统级内存不足

---

## S4: 转换超时

**日志关键字**：`timeout exceeded` / `Timed out` / `ETIMEDOUT` / `importFailProcess` / `IMPORT_TASK_INCOMPLETE`

**诊断**：检查任务是否被接收 → 未接收查队列积压 → 执行中超时检查文件大小 → IMPORTING超时3min重试最多3次

---

## S5: 加密文档

**日志关键字**：`CONVERSION_INVALID_PASSWORD` / `CONVERSION_UNSUPPORTED_ENCRYPTION` / `FETCH_REMOTE_FILE_DECRYPT_ERROR`

**诊断**：491=密码错误 492=不支持的加密方式(常见ODF) 1004=远端加密解密失败

---

## S6: 文件过大/内容超限

**日志关键字**：`CONTENT_TOOLARGE` / `CONTENT_LIMIT`

**诊断**：检查文件大小与限制(Word/Sheet 300MB, PPT 100MB, PDF 200-300MB) → 901/530-539为内容超限(页数/行列/单元格)

---

## S7: 权限/认证错误

**日志关键字**：`[apigateway_api_docs] errorCode` / `error.ejs` / `NO_RIGHT_EDIT_FILE` / `FILE_NOT_FOUND` / `DOC_TYPE_NOT_SUPPORT`

**诊断**：搜索apigateway_api_docs+docId → 确认token有效性 → 确认文件类型支持

---

## S8: WebSocket连接失败

**日志关键字**：`[ConnectionManager] auth error` / `AUTH_OTHER_ERROR` / `SESSION_EXPIRE` / `REACH_MAX_CONNECTIONS`

**诊断**：AUTH_OTHER_ERROR=token异常 SESSION_EXPIRE=JWT过期 REACH_MAX=连接数超限 → 检查代理idle timeout

---

## S9: License异常

**日志关键字**：`LICENSE_EXPIRE` / `LICENSE_NOT_AVAIL` / `LICENSE_EXCEED_USERS` / `NOT_ENABLE_EDIT` / `NOT_ENABLE_PREVIEW`

**诊断**：过期→续期 用户超限→扩容 能力未开启→License v5需单独授权

---

## S10: 文字乱码/字体缺失

**前端表现**：文档可以正常打开，但部分或全部文字显示为乱码、方块（□）或被替换为其他字体

**日志特征**：通常无明显错误日志，文档转换和打开流程均为成功状态（Result=1299）

**诊断**：此场景不是转换失败，而是文档中台缺少文档所需的字体文件。文档中台自身不内置任何商业/自定义字体，需要用户自行上传。

**引导用户操作**（按顺序执行）：
1. **上传字体文件**：在文档中台的**管理控制台**中上传文档所需的字体文件（.ttf/.otf/.ttc等），文档中台不提供字体，需用户自行准备
2. **重新上传文档**：字体上传后，必须**重新上传一份新文档**才能生效，不能使用历史已打开过的文档（历史文档的转换结果已缓存，不会重新转换）
3. **如仍乱码则重启服务**：若重新上传文档后仍然乱码，需要**重启文档中台服务**使新字体生效（字体加载在服务启动时完成）

---

## S11: SmartArt/组合图形图片缺失

**前端表现**：Excel 文档预览时部分图片显示为空白/404，或转换 PDF 失败（`modelOpUnSupport`）

**日志关键字**：
- Java 端（根因）：`Grpsp2pngConverter` / `Unknown image format` / `CellsException` / `BatchOBJS2PNGConverter`
- Node 端（表象）：`ENOENT Pictures/grpsp*.png` / `can not find document draft Attachment` / `processResult: false` / `modelOpResCode: modelOpUnSupport`

**诊断**：xlsx 内含 SmartArt 组合图形，Java 端 Aspose Cells 抽取子图时因图片格式不识别（EMF/WMF 等）报 `Unknown image format`，导致 PNG 未生成。Node 端读取时 ENOENT 只是表象，需反查 Java 日志找到 Aspose 报错才是根因。注意 Java 时间比 Node 早 8 小时。

**诊断步骤**：
1. Node 端搜 `ENOENT Pictures/grpsp` 或 `modelOpUnSupport`，找到失败时间和 taskId
2. 计算 Java 时间窗口（Node 时间 - 8h）
3. 在**全部 TaskServer 节点的全部 java-systemOut*.log** 中搜索 taskId
4. 如果 taskId 无匹配，**扩大搜索**：`Grpsp2pngConverter|Unknown image format|CellsException|SEVERE` + 时间窗口
5. 找到 SEVERE 报错后用 `get_context` 查看前后 20 行完整堆栈

**搜索遗漏防范**：
- ❌ 只搜了 java-systemOut.0.log 前几百行就判定"没有 Java 报错"——报错可能在文件中后段（如 1600 行以后）
- ❌ 只搜了一个 TaskServer 节点——任务可能被调度到不同节点
- ❌ 看到 Node 端 `ENOENT` 直接诊断为"文件丢失"——必须先确认 Java 端是否本应生成该文件
- ✅ 至少 3 轮搜索，覆盖全部节点和全部轮转文件

**引导用户验证**：
1. 在 Excel/WPS 中打开文件，找到 SmartArt 组合图形，**右键→取消组合**成普通图片后重新上传
2. 或尝试 Excel 中**另存为 PDF** 验证本机是否正常
