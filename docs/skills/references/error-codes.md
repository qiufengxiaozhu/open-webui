# 错误码与常见失败原因

## Result Error Code

| code | 含义 | 说明 |
|------|------|------|
| 1299 | 成功 | 任务正常完成（可能附带 detail 警告，不影响成功判定） |
| 1214 | 转换失败 | 转换过程中发生不可恢复错误（如 OOM、PDF 合并失败） |

## Done Execution Code（LocalConvertServiceImp 内部码）

| code | 含义 | 对应 Result code |
|------|------|-----------------|
| 200 | 转换成功 | → 1299 |
| 520 | 转换内部错误 | → 1214 |

## CLConvertor ExitCode

| code | errCode | 含义 |
|------|---------|------|
| 0 | 200 | CL 转换器正常退出 |
| 112 | 412 | CL 转换器非零退出（含不支持格式的警告，如 EMF/WMF/SmartArt/Chart），**不一定是失败**，需看最终 Result code |

## 常见失败原因分类

### 1. JVM 堆内存溢出 (OOM)

**日志特征：**
```
[PDFMergeConvertor] Convert task [<tid>] is failed in [<N>]ms on error Error: Error running instance method
java.lang.OutOfMemoryError: Java heap space
```

**常见场景：** ascompare 文档对比、大文件 PDF 合并
**排查方向：** 检查 JVM `-Xmx` 配置；检查文档是否包含大量图片/复杂排版

### 2. CL 转换器崩溃

**日志特征：**
```
[CLConvertor] ExitCode (code=<非0>;signal=<signal>;errCode:<非200>;id=<tid>[<docId>])
[CLConvertor] error spawnAsync(id=<tid>[<docId>])
Error: dist/taskserver/plugins/cl/ooxmlconvertor exited with non-zero code: <N>
```

**常见场景：** 文档内容异常、不支持的格式特性
**排查方向：** 检查 stderr 输出中的具体错误（如 parser error）；检查文件格式是否被正确识别

### 3. 下载失败

**日志特征：**
```
[ConvertFileService] [<docKey>] is failed to returned as source stream in <N>ms
[TextCompareConvertor] Compare task [<tid>] is failed with another file download failed
```

**常见场景：** 文件存储服务不可用、文件被删除、网络超时
**排查方向：** 检查 RestStore 请求的 HTTP 响应；检查 filez 存储服务状态

### 4. 超时

**日志特征：**
```
timeout exceeded
Timed out
ETIMEDOUT
```

**常见场景：** 大文件转换超过配置的 timeout（convert 默认 180s，exportDoc 默认 300s，cadConvert 默认 600s）
**排查方向：** 对照 default.json 中的 timeout 配置；评估是否需要调整超时时间

### 5. 文档内容异常

**日志特征：**
```
[CLConvertor] stderr: Entity: line 1: parser error : Document is empty
Output file size: <极小值，如 248>
```

**注意：** 若最终 Result code = 1299，则此类警告**不影响成功判定**

### 6. 格式不一致

**日志特征：**
```
Document's extension and real format are inconsistent
```

**说明：** 文件扩展名与实际格式不匹配，通常为 detail 警告，不一定导致失败

### 7. 认证失败（NewDocServer 侧）

**日志特征：**
```
[restAuth] authorizeFromHttp error ... 无效的用户token
[restAuth] auth fail, errorType=2
```

**说明：** 用户 token 过期或无效，请求在 NewDocServer 层即被拒绝，不会产生 TaskServer 任务

### 8. 存储服务 HTTP 错误（NewDocServer 侧）

**日志特征：**
```
[IntegrationUtil] rest store post request is rejected due to error = AxiosError: Request failed with status code <4xx/5xx>
```

**常见场景：** 集成存储服务配置错误、接口不兼容
