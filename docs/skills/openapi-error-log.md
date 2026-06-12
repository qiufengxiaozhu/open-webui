---
name: openApi故障分析
description: 分析 luoshu-server OpenAPI 错误日志，覆盖鉴权→参数校验→任务入队→下载→校验→转换→回调→下载全链路。当用户提到 OpenAPI 报错、转换失败、回调失败、任务超时时触发。场景详见 references/openapi-error-scenarios.md。
disable-model-invocation: true
---

# OpenAPI 错误日志分析

## 用户应提供

| 必须 | 建议 |
|------|------|
| 错误时间 | callback收到的code/msg |
| taskId | 调用的接口路径 |
| 错误现象描述 | — |

## 快速定位：通过 callback 判断

先看 callback（或 queryTaskStatus）的 `code` + `detail.msg` 快速定位：

| code | 含义 |
|------|------|
| TaskSuccessNotify | 成功，用contentId下载 |
| TaskFailNotify | 失败，看detail.msg |
| TaskHandingNotify | 处理中 |
| InvalidTaskId | 过期(>60min TTL) |

| detail.msg关键词 | 含义 |
|-----------------|------|
| conversion unknown | 通用转换失败 |
| conversion timeout | 超时 |
| conversion download err | 下载失败 |
| conversion invalid password | 加密 |
| conversion fotmat err | 格式/MIME错误 |
| conversion soffice err | 引擎错误 |
| content toolarge | 文件过大 |
| model op busy/fail/error | ModelOp失败 |

> 场景详细诊断见 `references/openapi-error-scenarios.md`

## 请求链路

```
① 鉴权 → HMAC签名+License+Quota
② 参数校验 → filename/fileUrl/callback/水印/白名单
③ 任务入队 → Redis锁+限流+Bull队列
④ 下载(TaskServer) → fileUrl HTTP下载(10s超时)
⑤ 文件校验 → 空文件/大小/MIME/加密检测
⑥ 转换执行 → CL/Aspose/Canvas/Puppeteer等引擎
⑦ 回调通知 → POST callbackUrl(5s超时,无重试)
⑧ 下载 → taskId+contentId获取结果
```

## 按现象定位

| 现象 | 阶段 | 日志关键字 |
|------|------|-----------|
| HTTP 401 | ①鉴权 | `verify token fail` |
| HTTP 412+ResCode | ②参数 | ResCode名称 |
| HTTP 200+ServerBusy | ③入队 | `get queue lock fail` |
| callback收到FAIL | ④⑤⑥ | `[ConvertWorker]` |
| callback未收到 | ⑦回调 | `task notify with error` |
| 下载报错 | ⑧下载 | `[downloadResult]` |

## 核心搜索关键字

| 阶段 | grep_log 搜索 pattern |
|------|----------------------|
| 全链路 | `<taskId>` |
| ①鉴权 | `verify token fail\|Token format is invalid\|Public API is disabled` |
| ③入队 | `get queue lock fail\|maxActiveTask\|concurrent exceeds` |
| ④下载 | `download error\|CONVERT_DOWNLOAD_ERROR\|0 bytes\|is too small` |
| ⑤校验 | `Invalid File mime type\|Password Protected\|FILE_TOO_LARGE` |
| ⑥转换 | `Result of Conversion task\|Catch Exception\|CLConvertor.*ExitCode` |
| ⑥Java层 | `ERROR\|Exception\|OutOfMemory`（java*.log） |
| ⑥ModelOp | `model op busy\|model op fail\|Worker.*killed\|modelOpUnSupport` |
| ⑥SmartArt转图 | `Grpsp2pngConverter\|Unknown image format\|BatchOBJS2PNGConverter`（java*.log） |
| ⑦回调 | `notify.*Success\|notify res fail\|task notify with error` |

## ResCode 速查

**401鉴权**：InvalidAuthHeader(400) InvalidAuthRepoID(401) InvalidAuthTimestamp(402) TokenIsInvalid(403) PublicApiIsDisable(464)

**412参数**：FilenameIsNull(410) CallbackIsNull(414) FileUrlNotAllowed(419) CallbackUrlNotAllowed(420) DocTypeNotSupport(421)

**入队**：TaskQueueCongestion(505) ServerBusy(506) NotSupportTask(513)

**回调/下载**：TaskSuccessNotify(509) TaskFailNotify(510) InvalidTaskId(423) ContentIdError(425)

## ConvertErrCode 速查

| Code | 值 | 含义 |
|------|----|------|
| CONVERSION_DONE | 200 | 完成 |
| FILE_TOO_LARGE | 413 | 过大 |
| FILE_INVALID_MIMETYPE | 415 | MIME无效 |
| INVALID_PASSWORD | 491 | 加密 |
| SOFFICE_BUSY | 493 | 引擎忙 |
| UNKNOWN | 520 | 未知 |
| CL_TIMEOUT | 521 | CL超时 |
| CORRUPTED_FILE | 529 | 损坏 |
| CONVERT_DOWNLOAD_ERROR | 1001 | 下载失败 |
| CONVERT_STANDALONE_BUSY | 1011 | 实例忙 |
| DOWNLOAD_FILE_TOO_SMALL | 1014 | 文件过小 |

## 任务判定

| 日志 | 成功 | 失败 |
|------|------|------|
| `Result of Conversion task [tid]: [code]` | 1299 | ≠1299 |
| `Done the execution ... with code [code]` | 200 | ≠200 |

## 超时配置

Bull 180s | CL/Java 300s | ModelOp 300s | JavaSA锁 120s | 下载 10s | 回调 5s(无重试) | 下载TTL 60min

## 文件大小限制

Word→PDF 50MB | Sheet→PDF 50MB | PPT→PDF 100MB | 水印 50MB | merge 400MB | OOXML最小 1KB

## 脚本辅助

```bash
python3 $SCRIPTS_DIR/analyze_openapi_failure.py --logDir $LOG_DIR --taskId <taskId>
```

参考：`references/openapi-error-scenarios.md` | `references/error-codes.md`
