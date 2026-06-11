# 错误码速查

## Result Error Code

| code | 含义 | 说明 |
|------|------|------|
| 1299 | 成功 | 可能附带detail警告 |
| 1203 | MIME/格式校验失败 | detail.id=415(MIME不匹配) 529(损坏)，常见：文件改名/内容为空/下载到错误内容 |
| 1214 | 转换失败(通用) | 不绑定特定异常，必须看日志确认原因 |

## Done Execution Code

| code | 含义 | → Result |
|------|------|----------|
| 200 | 成功 | → 1299 |
| 520 | 内部错误 | → 1214 |

## CLConvertor ExitCode

| code | errCode | 含义 |
|------|---------|------|
| 0 | 200 | 正常 |
| 112 | 412 | 非零退出（含格式警告），**不一定失败**，看最终Result |

## 常见失败模式

| 模式 | 日志关键字 | 说明 |
|------|-----------|------|
| OOM | `OutOfMemoryError: Java heap space` | 检查JVM -Xmx和文件大小 |
| CL崩溃 | `[CLConvertor] ExitCode (code=<非0>)` / `error spawnAsync` | 检查stderr和文件格式 |
| 下载失败 | `failed to returned as source stream` | 检查存储服务和网络 |
| 超时 | `timeout exceeded` / `ETIMEDOUT` | 对照timeout配置 |
| 文件异常 | `Document is empty` / `Output file size: <极小值>` | 最终Result=1299则仍成功 |
| 格式不一致 | `extension and real format are inconsistent` | 通常为警告 |
| 认证失败 | `[restAuth] auth fail` | token过期或无效 |
| 存储HTTP错误 | `rest store post request is rejected` | 集成存储配置错误 |

> 1215-1299范围错误码被ErrorUtil统一归一为CONVERSION_UNKNOWN
