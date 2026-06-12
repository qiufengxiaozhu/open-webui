---
name: 文档打开故障分析
description: 分析洛书服务文档打开（预览+编辑）错误日志。覆盖 HTTP入口→WebSocket→文档转换→Draft加载→保存发布→资源加载全阶段。当用户提到文档打不开、预览白屏、编辑报错、转换失败、保存失败时触发。场景详见 references/opendoc-error-scenarios.md。
disable-model-invocation: true
---

# 文档打开（预览+编辑）错误日志分析

## 用户应提供

| 必须 | 建议 |
|------|------|
| 错误时间（精确到分钟） | 文件类型(docx/xlsx等) |
| docId 或 taskId | 前端错误截图 |
| 错误现象描述 | — |

## 请求链路

```
① HTTP入口 → 认证/License/权限/格式校验
② WebSocket → JWT校验+连接数限制
③ 文档打开 → meta状态判断 → INACTIVE触发转换/ACTIVE直接加入/ERROR返回错误
④ 转换(TaskServer) → 下载源文件 → CL/Aspose转换 → 上传结果 → 回调DocsServer
⑤ Draft加载 → 拉取JSON/PDF draft
⑦ 保存/发布 → applyMsg → convert → uploadNewVersion
⑧ 资源加载 → 图片/字体/WMF懒转换
```

## 按现象定位

| 现象 | 阶段 | 日志关键字 |
|------|------|-----------|
| 错误页面(error.ejs) | ①HTTP入口 | `[apigateway_api_docs]` |
| 白屏/转圈不动 | ②③④ | `[ConnectionManager]` `[DocumentService]` |
| "转换服务不可用"+ErrorCode | ④转换失败 | `[ConvertWorker]` |
| "无法打开文档"+1203 | ④MIME校验失败 | `convert import error` |
| 一直转换中不结束 | ④IMPORTING超时 | `importFailProcess` |
| "无权编辑" | ①③权限 | `[apigateway_api_docs]` |
| License错误 | ①License | `LICENSE_EXPIRE` |
| 保存失败 | ⑦保存 | `[ExportDocWorker]` |
| 图片不显示 | ⑧资源 | `[attachment]` |
| draft加载失败 | ⑤Draft | `[DraftStorageService]` |
| 文字乱码/方块 | 字体缺失(非错误) | 无错误日志,需上传字体 |
| SmartArt图片缺失/404 | ④SmartArt转图失败 | `Grpsp2pngConverter` `Unknown image format`(java) |

> 场景详细诊断见 `references/opendoc-error-scenarios.md`

## 核心搜索关键字

| 阶段 | grep_log 搜索 pattern |
|------|----------------------|
| 全链路 | `<docId>` 或 `<taskId>` |
| ①入口 | `errorCode\|error\.ejs\|LICENSE_EXPIRE\|NO_RIGHT` |
| ②WebSocket | `AUTH_OTHER_ERROR\|SESSION_EXPIRE\|REACH_MAX` |
| ③打开 | `startConvert\|needConvert\|importFailProcess` |
| ④转换 | `Result of Conversion task\|Catch Exception\|CLConvertor.*ExitCode` |
| ④Java层 | `ERROR\|Exception\|OutOfMemory`（搜索java*.log） |
| ④下载 | `Download\|fileSize` |
| ⑤Draft | `Could not find draft\|Could not get draft` |
| ⑦保存 | `PUBLISH_REMOTE_ERROR\|applyMessages.*error` |

## 错误码速查

| code | 含义 |
|------|------|
| 1299 | 成功 |
| 1203 | MIME/格式错误（detail 415=MIME不匹配, 529=损坏） |
| 1214 | 转换失败（通用，必须看日志确认原因） |

| DocsErrorCode | ConvertErrCode | 含义 |
|---------------|---------------|------|
| CONVERSION_UNKNOWN | 520,525,526,1215-1299 | 未知转换错误 |
| CONVERSION_TIMEOUT | 495,521,1006 | 超时 |
| CONVERSION_FOTMAT_ERR | 415,529 | MIME/格式 |
| CONVERSION_DOWNLOAD_ERR | 1001 | 下载失败 |
| CONVERSION_INVALID_PASSWORD | 491 | 加密密码错误 |
| CONVERSION_SERVER_BUSY | 493,1011 | 引擎繁忙 |
| CONTENT_TOOLARGE | 413 | 文件过大 |
| DOWNLOAD_FILE_TOO_SMALL | 1014 | 下载文件过小 |

## Meta 状态机

| 状态 | 行为 |
|------|------|
| INACTIVE | 触发转换 |
| IMPORTING | 等待中，3min后importFailProcess，最多重试3次 |
| ACTIVE | 直接加入会话 |
| ERROR | 返回持久化errorCode |

## 任务判定

| 日志 | 成功 | 失败 |
|------|------|------|
| `Result of Conversion task [tid]: [code]` | 1299 | ≠1299 |
| `Done the execution ... with code [code]` | 200 | ≠200 |

> CLConvertor ExitCode code=112 可能伴随parser error，但最终Result=1299则任务**成功**。

## 超时配置

Convert 180s | ExportDoc 300s | CL/Java 300s | IMPORTING 3min×3次(max60min) | WS disconnect 60s

## 文件大小限制

Word 300MB | Sheet 300MB | PPT 100MB | PDF 200-300MB

## 脚本辅助

```bash
python3 $SCRIPTS_DIR/analyze_task_failure.py --logDir $LOG_DIR --docId <docId>
python3 $SCRIPTS_DIR/analyze_task_failure.py --logDir $LOG_DIR --taskId <taskId>
```

参考：`references/opendoc-error-scenarios.md` | `references/error-codes.md`
