# OpenAPI 报错场景速查

> **快速定位**：先看 callback 的 `code` + `detail.msg` 判断错误类型，再看日志确认细节。

## callback code 速判

| code | 含义 |
|------|------|
| TaskSuccessNotify/ConvertSuccessNotify | 成功，用contentId下载 |
| TaskFailNotify/ConvertFailNotify | 失败，看detail.msg |
| TaskHandingNotify | 处理中 |
| InvalidTaskId | taskId无效/过期(>60min TTL) |

## detail.msg 分类

| msg关键词 | 场景 |
|----------|------|
| conversion unknown | S1 转换失败(通用) |
| conversion download err | S2 下载失败 |
| conversion timeout | S3 超时 |
| conversion invalid password | S4 加密 |
| conversion fotmat err | S5 格式错误 |
| conversion soffice err | S6 引擎错误 |
| content toolarge | S7 文件过大 |
| model op busy/fail/error | S8 ModelOp失败 |

## 场景索引

| 编号 | 报错类型 | msg特征 | 错误码 |
|-----|---------|--------|-------|
| S1 | 转换失败(通用) | conversion unknown | 1214,520,905 |
| S2 | 源文件下载失败 | conversion download err | 1001,1014 |
| S3 | 转换超时 | conversion timeout | 495,521,1006 |
| S4 | 加密文档 | conversion invalid password | 491,492 |
| S5 | MIME/格式错误 | conversion fotmat err | 415,529 |
| S6 | 引擎繁忙/错误 | conversion soffice err/ServerBusy | 493,1011,505,506 |
| S7 | 文件过大 | content toolarge | 413,901,530-539 |
| S8 | ModelOp失败 | model op busy/fail/error | — |
| S8a | SmartArt/组合图形转图失败 | model op busy + modelOpUnSupport | 1299(表面成功码) |
| S9 | 回调未收到 | 无回调 | — |
| S10 | 下载失败 | HTTP响应错误 | 423-426,502 |
| S11 | 鉴权/参数错误 | HTTP同步返回 | 400-421 |

---

## S1: 转换失败(通用)

**日志关键字**：`[ConvertWorker] Catch Exception` / `[CLConvertor] ExitCode` / `[CommonJavaConvertor] Error` / `UnsupportedFileFormatException`

**典型子场景**：

| 异常 | 引擎 | 含义 |
|------|------|------|
| `UnsupportedFileFormatException: Unsupported file format: Unknown` | Aspose.Words / Aspose.Cells | 文件头魔数不匹配任何已知格式，常见原因：文件损坏/下载不完整/扩展名与实际格式不一致 |
| `Document is empty` + `ExitCode code=112` | CLConvertor(libxml2) | XML 解析器判定文件内容为空 |
| `CellsException: Unknown image format` | Aspose.Cells (Grpsp2pngConverter) | xlsx 内嵌 SmartArt 图形转图失败（见 S8a 详情） |

**诊断**：搜索taskId找 `Catch Exception` 或 `ExitCode` → 看 java*.log 中异常堆栈 → 检查fileSize → 1214 不能绑定特定异常 → 如果 `UnsupportedFileFormatException`，需确认文件实际格式

**用户验证**：获取下载日志 → 小文件用文本编辑器看内容 → WPS/Office打开 → 检查扩展名一致性 → `xxd 文件 | head` 看首字节确认真实格式

---

## S2: 源文件下载失败

**日志关键字**：`download file error` / `failed to returned as source stream` / `fileSize is 0` / `is too small`

**诊断**：搜索Download+taskId → 常见原因：ENOTFOUND/ETIMEDOUT/SSL错误/HTTP非200 → 下载超时10s无重试

**用户验证**：curl请求fileUrl → 对比文件大小 → 小文件看内容

---

## S3: 转换超时

**日志关键字**：`timeout exceeded` / `time to cancel task` / `ETIMEDOUT`

**诊断**：确认任务接收时间和超时时间 → 默认：Convert 3min, CL/Java 5min, ModelOp 5min, CAD 10min → 未被消费查队列积压

---

## S4: 加密文档

**日志关键字**：`INVALID_PASSWORD` / `Password Protected` / `UNSUPPORTED_ENCRYPTION`

**诊断**：491=密码错误 492=不支持加密方式 1004=远端解密失败

---

## S5: MIME/格式错误

**日志关键字**：`Invalid File mime type` / `no supported mime type` / `correct mime type`

**诊断**：文件扩展名与实际内容不匹配 → 搜索correct mime type看自动修正 → MIME检测基于Magic Bytes

**用户验证**：检查文件是否改名 → 文本编辑器看文件头

---

## S6: 引擎繁忙/错误

**日志关键字**：`standalone convertor is busy` / `get queue lock fail` / `maxActiveTask` / `SOFFICE_BUSY`

**诊断**：ServerBusy=Redis锁失败 TaskQueueCongestion=任务数超限 1011=实例全忙 493=Symphony全忙

---

## S7: 文件过大

**日志关键字**：`CONTENT_TOOLARGE` / `FILE_TOO_LARGE` / `CONTENT_LIMIT`

**诊断**：对照限制(Word/Sheet 50MB, PPT 100MB, merge 400MB) → 530-539为内容超限

---

## S8: ModelOp失败

**日志关键字**：`model op busy` / `model op with error` / `Worker killed because of timeout` / `Print writer To PDF Fail`

**诊断**：busy=队列满 fail=执行失败 error=Canvas异常 → 搜索step1(CONVERT)+step2(MODEL_OP)两步日志

---

## S8a: SmartArt/组合图形转图失败（Aspose Unknown image format）

**callback 特征**：`TaskFailNotify` + `detail.msg`: `model op busy` 或 `modelOpUnSupport`

**日志关键字**（按诊断优先级排列）：
- Java 端（真正根因）：`Grpsp2pngConverter` / `Unknown image format` / `CellsException` / `BatchOBJS2PNGConverter`
- Node 端（表象）：`processResult: false` / `ENOENT Pictures/grpsp*.png` / `SaveAs: target file not exist` / `modelOpResCode: modelOpUnSupport`

**典型案例**（Excel→PDF，xlsx 含 SmartArt 组合图形）：
- xlsx 内嵌 SmartArt 组合图形（grpsp），Java 端 Aspose Cells 在抽取内部子图时报 `Unknown image format`（EMF/WMF 等冷门格式 Aspose 不识别）
- Java 端转图失败 → 未生成 `grpsp1-5.png` → Node 端 CanvasProcess 读取时 ENOENT → PDF 输出失败 → 回调 `model op busy`

**关键诊断陷阱**：
1. **因果倒置**：Node 端的 `ENOENT Pictures/grpsp*.png` 只是**表象**，不是根因。真正原因在 Java 端的 Aspose 报错
2. **Java 时区差 8 小时**：Java 日志时间 = Node 日志时间 - 8h（Java UTC+0，Node UTC+8），不要因时间"太早"忽略
3. **需搜索全部 TaskServer 节点**：日志可能在 TaskServer_4 而不是 TaskServer_2
4. **Excel 转 PDF 子链路经过 Aspose**：主链路是 CL+Canvas，但抽取 SmartArt 图片的子链路走 BatchOBJS2PNGConverter → Grpsp2pngConverter → Aspose Cells

**诊断步骤**：
1. 搜索 taskId 找到 Node 端的 `modelOpUnSupport` / `processResult: false`
2. 从 Node 日志提取 ModelOp 子任务 ID（如 `4aafc1d1-...`）
3. **计算 Java 时间窗口**：Node 时间 - 8h = Java 时间（如 Node 10:50 → Java 02:50 AM）
4. 用子任务 ID + 时间窗口在**所有 TaskServer 的全部 java-systemOut*.log** 中搜索
5. 如果 taskId 无匹配，**立即用功能关键词扩大搜索**：`Grpsp2pngConverter|Unknown image format|CellsException|SEVERE`
6. 找到 `Grpsp2pngConverter` + `CellsException: Unknown image format` → 确认根因
7. 用 `get_context` 查看报错前后 20 行，确认 taskId 和转换路径

**搜索遗漏防范清单**（基于历史教训）：
- ❌ 常见错误 1：只搜了 java-systemOut.0.log 前 400 行就认为"没有 Java 报错"——实际报错在 1600 行之后
- ❌ 常见错误 2：`grep_log` 返回 `total: 30, limit: 30` 时停止搜索——结果被截断，需缩小范围重搜
- ❌ 常见错误 3：只搜 `grpsp` 小写片段而漏掉了 `Grpsp2pngConverter` 全类名的匹配——应同时用多个关键词
- ❌ 常见错误 4：只搜了 TaskServer_4 就下结论——任务可能分派到 TaskServer_2 / TaskServer_3 / TaskServer_5
- ❌ 常见错误 5：看到 Node 端 `ENOENT` 就直接诊断为"文件丢失"——必须先反查 Java 端确认文件是否本应由 Java 生成
- ❌ 常见错误 6：第 1 轮搜 `BatchOBJS2PNGConverter` 返回 30 条全是 5/19 的 INFO 级旧日志（行号 70-400），就误认为没有异常——真正的 5/25 SEVERE 错误（`Grpsp2pngConverter` + `Unknown image format`）在 1600 行之后被旧 INFO "挤掉"了
- ✅ 正确做法：**至少 3 轮 grep_log**（taskId → SEVERE/Exception → 功能关键词），覆盖全部 TaskServer 节点和全部轮转文件
- ✅ 截断自检：每次 grep_log 返回后，检查 3 项：①条数=30？→ 截断 ②最新条目日期覆盖事件日？→ 关键词被淹没 ③日志级别全是 INFO？→ SEVERE 被挤掉

**引导用户验证**：
1. 在 Excel/WPS 中打开文件，找到含图片的 SmartArt 组合图形，**右键→取消组合**成普通图片后重新上传
2. 若取消组合不便，尝试 Excel 中**另存为 PDF** 看本机是否正常 — 若本机也异常说明 SmartArt 内图片已损坏

---

## S9: 回调未收到

**日志关键字**：`task notify with error` / `notify res fail` / `failed to notify with wrong taskCtx`

**诊断**：先确认任务是否完成 → 回调超时5s无重试 → 检查callbackUrl白名单 → 用queryTaskStatus兜底

**用户验证**：确认callback接口可达且5s内响应 → 用queryTaskStatus查询

---

## S10: 下载失败

| code | 含义 |
|------|------|
| InvalidTaskId(423) | 超过60min TTL |
| ContentIdError(425) | 文件不存在 |
| ContentIdIsNull(426) | 缺参数 |
| AccessOtherRepoIsNotAllowed(424) | 无权下载 |
| DownloadErr(502) | 存储异常 |

---

## S11: 鉴权/参数错误

HTTP同步返回，无taskId，不触发callback。

**401鉴权**：HMAC签名不匹配/repo未启用/Authorization格式错误(需repoId:appId:token三段)

**412参数**：FilenameIsNull/FileUrlNotAllowed/CallbackUrlNotAllowed/DocTypeNotSupport/水印参数错误
