#!/usr/bin/env python3
"""
洛书 OpenAPI 任务失败分析脚本。

扫描 NewDocServer / TaskServer 日志，按 taskId 或时间段定位 OpenAPI 任务失败原因，
输出：阶段、错误码、报错信息、原因分类、完整时间线。

用法:
    python3 analyze_openapi_failure.py --logDir /path/to/logs --taskId <taskId>
    python3 analyze_openapi_failure.py --logDir /path/to/logs --timeFrom "2026-05-22 14:00:00" --timeTo "2026-05-22 15:00:00"
    python3 analyze_openapi_failure.py --logDir /path/to/logs --timeFrom "2026-05-22 14:00:00" --timeTo "2026-05-22 15:00:00" --all
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S.%f"

# ─── 日志目录发现 ───

COMPONENT_PREFIXES = ("TaskServer_", "NewDocServer_", "OTServer_")


def _is_log_dir(p):
    if not p.is_dir():
        return False
    return any(c.name.startswith(COMPONENT_PREFIXES) and c.is_dir() for c in p.iterdir())


def discover_log_dirs(roots, max_depth=5):
    dirs = []
    for r in roots:
        r = Path(r)
        if not r.exists():
            print(f"[WARN] 路径不存在: {r}", file=sys.stderr)
            continue
        if _is_log_dir(r):
            dirs.append(r)
        else:
            for depth in range(1, max_depth + 1):
                pattern = "/".join(["*"] * depth)
                for sub in r.glob(pattern):
                    if sub.is_dir() and _is_log_dir(sub):
                        dirs.append(sub)
            dirs = list(dict.fromkeys(dirs))
    return dirs


def find_log_files(log_dirs, component_prefix, log_pattern="combined-*.log*"):
    files = []
    for d in log_dirs:
        for comp_dir in sorted(d.iterdir()):
            if comp_dir.is_dir() and comp_dir.name.startswith(component_prefix):
                for f in sorted(comp_dir.glob(log_pattern)):
                    files.append(f)
    return files


# ─── 日志解析 ───

_RE_TIMESTAMP = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')
_RE_MESSAGE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')
_RE_LEVEL = re.compile(r'"level"\s*:\s*"([^"]+)"')


def parse_log_line(line):
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
        return obj.get("timestamp", ""), obj.get("message", ""), obj.get("level", "")
    except (json.JSONDecodeError, ValueError):
        pass
    ts_m = _RE_TIMESTAMP.search(line)
    msg_m = _RE_MESSAGE.search(line)
    lvl_m = _RE_LEVEL.search(line)
    if ts_m and msg_m:
        msg = msg_m.group(1).replace('\\"', '"').replace("\\n", "\n")
        return ts_m.group(1), msg, lvl_m.group(1) if lvl_m else ""
    return None


def read_log_entries(fpath):
    entries = []
    buf = []
    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if not stripped:
                    if buf:
                        buf.append(raw_line)
                    continue
                if stripped.startswith('{"timestamp"'):
                    if buf:
                        entries.append("".join(buf))
                    buf = [raw_line]
                else:
                    if buf:
                        buf.append(raw_line)
            if buf:
                entries.append("".join(buf))
    except OSError as e:
        print(f"[WARN] 读取文件失败 {fpath}: {e}", file=sys.stderr)
    return entries


def parse_multiline_entry(entry):
    first_line = entry.split("\n", 1)[0].strip()
    parsed = parse_log_line(first_line)
    if parsed:
        return parsed
    flat = entry.replace("\n", "\\n")
    try:
        obj = json.loads(flat)
        return obj.get("timestamp", ""), obj.get("message", ""), obj.get("level", "")
    except (json.JSONDecodeError, ValueError):
        pass
    ts_m = _RE_TIMESTAMP.search(entry)
    lvl_m = _RE_LEVEL.search(entry)
    msg_m = _RE_MESSAGE.search(entry)
    if ts_m:
        if msg_m:
            msg = msg_m.group(1).replace('\\"', '"')
            rest_start = msg_m.end()
            rest = entry[rest_start:].rsplit('"}', 1)[0] if '"}' in entry[rest_start:] else ""
            full_msg = msg + rest
        else:
            full_msg = entry[ts_m.end():].strip()
        return ts_m.group(1), full_msg, lvl_m.group(1) if lvl_m else ""
    return None


def parse_ts(ts_str):
    try:
        return datetime.strptime(ts_str, TIMESTAMP_FMT)
    except ValueError:
        try:
            return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def ts_in_range(ts_str, ts_from, ts_to):
    if ts_from is None and ts_to is None:
        return True
    ts = parse_ts(ts_str)
    if ts is None:
        return True
    if ts_from and ts < ts_from:
        return False
    if ts_to and ts > ts_to:
        return False
    return True


# ─── 正则模式（OpenAPI 特有） ───

# NewDocServer 侧 — OpenAPI 入口
RE_CONVERT_REQ = re.compile(
    r"\[convertReqHandler\].*?task.*?(error|fail).*?([0-9a-f-]{36})?",
    re.IGNORECASE
)
RE_ADD_TASK_OK = re.compile(
    r"add.*?task.*?taskId['\"]?\s*[:=]\s*['\"]?([0-9a-f-]{36})",
    re.IGNORECASE
)
RE_FORMAT_RES = re.compile(
    r'"taskId"\s*:\s*"([0-9a-f-]{36})".*?"code"\s*:\s*"(\w+)"'
)
RE_OPEN_SERVICE_AUTH = re.compile(
    r"(verify token fail|Token format is invalid|repoId not enabled|"
    r"authorization timestamp expired|Secret of public api is null|"
    r"Public API is disabled|Authorization header is null)"
)
RE_LICENSE_CHECK = re.compile(
    r"(License not found|License is invalid|License has expired)"
)
RE_QUEUE_LIMIT = re.compile(
    r"(get queue lock fail|maxActiveTask|concurrent exceeds license limit|"
    r"TaskQueueCongestion|ServerBusy)",
    re.IGNORECASE
)
RE_NOTIFY = re.compile(
    r"\[AbstractOpenService\].*notify.*?(error|fail)|notify res fail",
    re.IGNORECASE
)
RE_NOTIFY_SUCCESS = re.compile(
    r"notify.*?taskId.*?([0-9a-f-]{36}).*?(Success|Fail)",
    re.IGNORECASE
)
RE_DOWNLOAD_RESULT = re.compile(
    r"\[downloadResult\].*?(error|taskId|contentId)",
    re.IGNORECASE
)
RE_MODEL_OP_HANDLER = re.compile(
    r"\[modelOpHandler\]|\[HttpModelOpServiceImpl\]"
)
RE_MODEL_OP_MSG = re.compile(
    r"(model op busy|model op fail|model op error|get draft success|"
    r"export doc busy|clearTempData fail)"
)

# TaskServer 侧 — 转换执行
RE_RECEIVE_TASK = re.compile(
    r"\[Task Server\]\[\d+\] Receive Task : \[([0-9a-f-]+) / (\w+)\]"
)
RE_CONVERT_REQUEST = re.compile(
    r"\[LocalConvertServiceImp\] Get convert request of task \[([0-9a-f-]+)\]"
)
RE_DOWNLOAD_DONE = re.compile(
    r"\[LocalConvertServiceImp\] Download of task \[([0-9a-f-]+)\].*?done in (\d+)ms"
)
RE_DOWNLOAD_ERROR = re.compile(
    r"\[LocalConvertServiceImp\] Download of task.*?error.*?\[([0-9a-f-]+)\]"
)
RE_FILESIZE = re.compile(
    r"\[LocalConvertServiceImp\] tid:([0-9a-f-]+) fileSize is (\d+) B"
)
RE_VALIDATE_FAIL = re.compile(
    r"\[LocalConvertServiceImp\].*?Failed the validation.*?(\d+)"
)
RE_DONE_EXECUTION = re.compile(
    r"\[LocalConvertServiceImp\] Done the execution of conversion task "
    r"\[([0-9a-f-]+)\].*?in (\d+)ms with code \[(\d+)\]"
)
RE_RESULT = re.compile(
    r"\[ConvertWorker\] Result of Conversion task \[([0-9a-f-]+)\]:\s+\[(\d+)\]"
)
RE_COMPLETED = re.compile(
    r"\[ConvertWorker\] Conversion task \[([0-9a-f-]+)\] is completed in (\d+)ms"
)

# 转换器
RE_TEXT2PDF = re.compile(r"\[Text2PdfConvertor\].*?\[([0-9a-f-]+)\]")
RE_WEBPRINT = re.compile(r"\[WebPrintConvertor\].*?([0-9a-f-]+)")
RE_ASPOSE = re.compile(r"\[AConvertor\].*?([0-9a-f-]+)")
RE_CL_EXIT = re.compile(
    r"\[CLConvertor\] ExitCode \(code=(\d+);.*?errCode:(\d+);id=([0-9a-f-]+)"
)
RE_CL_TIMEOUT = re.compile(
    r"\[CLConvertor\].*?cancel task.*?([0-9a-f-]+).*?time is over"
)
RE_JAVA_SA = re.compile(
    r"\[JavaSAConvertor\].*?ExitCode.*?([0-9a-f-]+)"
)
RE_JAVA_SA_BUSY = re.compile(
    r"java standalone convertor is busy"
)
RE_PDF_WM = re.compile(
    r"\[PDFWatermarkConvertor\].*?(error|fail|empty)",
    re.IGNORECASE
)
RE_MIME_ERR = re.compile(
    r"Invalid File mime type|no supported mime type|MIME_TYPE_MODIFIED|correct mime type"
)
RE_ENCRYPT_ERR = re.compile(
    r"Password Protected|UNSUPPORTED_ENCRYPTION|INVALID_PASSWORD|isPwdProtected"
)

# ModelOp TaskServer 侧
RE_MODEL_SERVICE_WORKER = re.compile(
    r"\[ModelServiceWorker\]|\[OpenModelWorker\]"
)
RE_MODEL_MANAGER = re.compile(
    r"\[ModelManager\].*?(killed|timeout|Error is returned|Failed to process)"
)
RE_CANVAS_MODEL = re.compile(
    r"\[CanvasModelService\].*?(Fail|Error)"
)


# ─── 任务数据结构 ───

class OpenAPITask:
    def __init__(self, tid):
        self.tid = tid
        self.stage = ""
        self.res_code = ""
        self.convert_err_code = ""
        self.result_code = ""
        self.status = ""
        self.file_size = 0
        self.download_ms = 0
        self.duration_ms = 0
        self.error_messages = []
        self.timeline = []
        self.engine = ""
        self.is_model_op = False

    @property
    def is_success(self):
        if self.status:
            return self.status.upper() == "SUCCESS"
        if self.result_code:
            return self.result_code == "1299"
        if self.res_code:
            return self.res_code in ("Ok", "ConvertSuccessNotify", "TaskSuccessNotify")
        return False

    def add_timeline(self, ts, event):
        self.timeline.append((ts, event))

    def add_error(self, msg):
        if msg and msg not in self.error_messages:
            self.error_messages.append(msg)


# ─── 扫描 NewDocServer 日志 ───

def scan_docserver_openapi(log_files, task_ids, ts_from, ts_to):
    tasks = {}

    for fpath in log_files:
        entries = read_log_entries(fpath)
        for entry in entries:
            parsed = parse_multiline_entry(entry)
            if parsed is None:
                continue
            ts_str, msg, level = parsed
            if not ts_in_range(ts_str, ts_from, ts_to):
                continue

            for tid in task_ids:
                if tid in msg:
                    if tid not in tasks:
                        tasks[tid] = OpenAPITask(tid)
                    task = tasks[tid]

                    if RE_OPEN_SERVICE_AUTH.search(msg):
                        task.stage = "① 鉴权"
                        task.add_error(msg.strip()[:200])
                        task.add_timeline(ts_str, "鉴权失败")
                    elif RE_LICENSE_CHECK.search(msg):
                        task.stage = "① License"
                        task.add_error(msg.strip()[:200])
                        task.add_timeline(ts_str, "License 校验失败")
                    elif RE_QUEUE_LIMIT.search(msg):
                        task.stage = "③ 入队"
                        task.add_error(msg.strip()[:200])
                        task.add_timeline(ts_str, "队列限流/锁失败")
                    elif RE_MODEL_OP_MSG.search(msg):
                        m = RE_MODEL_OP_MSG.search(msg)
                        task.is_model_op = True
                        event = m.group(1)
                        if "success" in event.lower():
                            task.add_timeline(ts_str, f"ModelOp: {event}")
                        else:
                            task.stage = "⑥ ModelOp"
                            task.add_error(event)
                            task.add_timeline(ts_str, f"ModelOp 失败: {event}")
                    elif RE_NOTIFY.search(msg):
                        task.add_timeline(ts_str, "回调通知发送")
                        if "error" in msg.lower() or "fail" in msg.lower():
                            task.stage = task.stage or "⑦ 回调"
                            task.add_error("回调发送失败")
                            task.add_timeline(ts_str, "回调发送失败")
                    elif RE_DOWNLOAD_RESULT.search(msg):
                        if "error" in msg.lower():
                            task.stage = "⑧ 下载"
                            task.add_error(msg.strip()[:200])
                            task.add_timeline(ts_str, "下载接口错误")
                    elif "convertReqHandler" in msg:
                        if "error" in msg.lower() or "fail" in msg.lower():
                            task.stage = "③ 入队"
                            task.add_error(msg.strip()[:200])
                            task.add_timeline(ts_str, "入队异常")
                        else:
                            task.add_timeline(ts_str, "请求处理")
                    else:
                        task.add_timeline(ts_str, _abbreviate(msg))

    return tasks


# ─── 扫描 TaskServer 日志 ───

def scan_taskserver_openapi(log_files, task_ids, ts_from, ts_to):
    tasks = {}

    for fpath in log_files:
        entries = read_log_entries(fpath)
        for entry in entries:
            parsed = parse_multiline_entry(entry)
            if parsed is None:
                continue
            ts_str, msg, level = parsed
            if not ts_in_range(ts_str, ts_from, ts_to):
                continue

            for tid in task_ids:
                if tid not in msg:
                    continue
                if tid not in tasks:
                    tasks[tid] = OpenAPITask(tid)
                task = tasks[tid]

                m = RE_RECEIVE_TASK.search(msg)
                if m and m.group(1) == tid:
                    task.add_timeline(ts_str, f"TaskServer 接收 [{m.group(2)}]")
                    continue

                m = RE_DOWNLOAD_DONE.search(msg)
                if m and m.group(1) == tid:
                    task.download_ms = int(m.group(2))
                    task.add_timeline(ts_str, f"源文件下载完成 ({m.group(2)}ms)")
                    continue

                if RE_DOWNLOAD_ERROR.search(msg):
                    task.stage = task.stage or "④ 下载"
                    task.add_error("源文件下载失败")
                    task.add_timeline(ts_str, "源文件下载失败")
                    continue

                m = RE_FILESIZE.search(msg)
                if m and m.group(1) == tid:
                    task.file_size = int(m.group(2))
                    task.add_timeline(ts_str, f"文件大小 {_fmt_size(int(m.group(2)))}")
                    continue

                if RE_VALIDATE_FAIL.search(msg):
                    m2 = RE_VALIDATE_FAIL.search(msg)
                    task.stage = task.stage or "⑤ 校验"
                    task.convert_err_code = m2.group(1)
                    task.add_error(f"文件校验失败 code={m2.group(1)}")
                    task.add_timeline(ts_str, f"文件校验失败 code={m2.group(1)}")
                    continue

                m = RE_DONE_EXECUTION.search(msg)
                if m and m.group(1) == tid:
                    task.convert_err_code = m.group(3)
                    task.duration_ms = int(m.group(2))
                    task.add_timeline(ts_str, f"转换完成 code={m.group(3)} ({m.group(2)}ms)")
                    continue

                m = RE_RESULT.search(msg)
                if m and m.group(1) == tid:
                    task.result_code = m.group(2)
                    task.add_timeline(ts_str, f"任务结果 code={m.group(2)}")
                    continue

                m = RE_COMPLETED.search(msg)
                if m and m.group(1) == tid:
                    task.duration_ms = int(m.group(2))
                    continue

                m = RE_CL_EXIT.search(msg)
                if m and m.group(3) == tid:
                    task.add_timeline(ts_str, f"CLConvertor ExitCode={m.group(1)} errCode={m.group(2)}")
                    if m.group(1) != "0":
                        task.add_error(f"CL 转换异常 exitCode={m.group(1)} errCode={m.group(2)}")
                    continue

                if RE_CL_TIMEOUT.search(msg):
                    task.stage = task.stage or "⑥ 转换超时"
                    task.add_error("CL 转换超时")
                    task.add_timeline(ts_str, "CL 转换超时 (cancel)")
                    continue

                if RE_JAVA_SA_BUSY.search(msg):
                    task.stage = task.stage or "⑥ 转换繁忙"
                    task.add_error("Java Standalone 繁忙 (Redis 锁满)")
                    task.add_timeline(ts_str, "JavaSA 繁忙")
                    continue

                if RE_MIME_ERR.search(msg):
                    task.stage = task.stage or "⑤ MIME"
                    task.add_error("MIME 类型校验异常")
                    task.add_timeline(ts_str, "MIME 类型校验")
                    continue

                if RE_ENCRYPT_ERR.search(msg):
                    task.stage = task.stage or "⑤ 加密"
                    task.add_error("加密文档检测")
                    task.add_timeline(ts_str, "加密文档检测")
                    continue

                if RE_MODEL_MANAGER.search(msg):
                    m2 = RE_MODEL_MANAGER.search(msg)
                    task.stage = task.stage or "⑥ ModelOp"
                    task.add_error(f"ModelManager: {m2.group(1)}")
                    task.add_timeline(ts_str, f"ModelManager: {m2.group(1)}")
                    continue

                if RE_CANVAS_MODEL.search(msg):
                    task.stage = task.stage or "⑥ Canvas"
                    task.add_error("CanvasModelService 渲染失败")
                    task.add_timeline(ts_str, "Canvas 渲染失败")
                    continue

                if RE_MODEL_SERVICE_WORKER.search(msg):
                    task.add_timeline(ts_str, _abbreviate(msg))
                    if level == "error":
                        task.stage = task.stage or "⑥ ModelOp"
                        task.add_error(_abbreviate(msg))
                    continue

                if level == "error":
                    task.add_error(_abbreviate(msg))
                    task.add_timeline(ts_str, f"[ERROR] {_abbreviate(msg)}")

    return tasks


# ─── 全量扫描（无 taskId 时按时间段） ───

def scan_all_openapi_tasks(ds_files, ts_files, ts_from, ts_to):
    """扫描所有 OpenAPI 相关日志，提取 taskId。"""
    task_ids = set()

    re_taskid_in_openapi = re.compile(
        r"(?:convertReqHandler|AbstractOpenConvertService|HttpConvertServiceImpl|"
        r"modelOpHandler|HttpModelOpServiceImpl|OpenServiceAuth|downloadResult|"
        r"TaskStatusServiceImpl|openApiStatistics).*?([0-9a-f-]{36})"
    )

    for fpath in ds_files:
        entries = read_log_entries(fpath)
        for entry in entries:
            parsed = parse_multiline_entry(entry)
            if parsed is None:
                continue
            ts_str, msg, _ = parsed
            if not ts_in_range(ts_str, ts_from, ts_to):
                continue
            m = re_taskid_in_openapi.search(msg)
            if m:
                task_ids.add(m.group(1))

    return task_ids


# ─── Java 日志扫描 ───

def scan_java_logs(log_dirs, task_ids, ts_from, ts_to):
    java_errors = {}
    files = []
    for d in log_dirs:
        for comp_dir in sorted(d.iterdir()):
            if comp_dir.is_dir():
                for f in sorted(comp_dir.glob("java*.log*")):
                    files.append(f)

    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    for tid in task_ids:
                        if tid in line and ("ERROR" in line or "Exception" in line or "OutOfMemory" in line):
                            if tid not in java_errors:
                                java_errors[tid] = []
                            java_errors[tid].append(line.strip()[:300])
        except OSError:
            pass

    return java_errors


# ─── 失败原因分类 ───

CONVERT_ERR_CODES = {
    "413": "文件过大 (FILE_TOO_LARGE)",
    "415": "MIME 类型无效 (FILE_INVALID_MIMETYPE)",
    "491": "加密文档/密码错误 (INVALID_PASSWORD)",
    "492": "不支持的加密方式 (UNSUPPORTED_ENCRYPTION)",
    "493": "Symphony 繁忙 (SOFFICE_BUSY)",
    "495": "Symphony 超时 (SOFFICE_RUNTIME_ERROR)",
    "496": "Symphony 不可用 (SOFFICE_UNAVILABLE)",
    "501": "转换类型不支持 (CONVERT_NOT_SUPPORTED)",
    "520": "未知错误 (UNKNOWN)",
    "521": "CL 转换超时 (CL_TIMEOUT)",
    "522": "IO 异常 (IO_EXCEPTION)",
    "523": "单页渲染超时 (SINGLE_PAGE_OVERTIME)",
    "529": "文件损坏 (CORRUPTED_FILE)",
    "901": "内容超限 (CONTENT_LIMIT)",
    "905": "转换未知错误 (CONVERSION_UNKNOWN)",
    "1001": "源文件下载失败 (CONVERT_DOWNLOAD_ERROR)",
    "1002": "结果上传失败 (CONVERT_UPLOAD_ERROR)",
    "1004": "远端文件解密失败",
    "1011": "Standalone/Puppeteer 繁忙",
    "1012": "Java Standalone 转换错误",
    "1014": "下载文件过小 (DOWNLOAD_FILE_TOO_SMALL)",
}

RES_CODES_CLIENT = {
    "InvalidAuthHeader", "InvalidAuthRepoID", "InvalidAuthTimestamp",
    "TokenIsInvalid", "AuthHeaderIsNull", "PublicApiIsDisable", "SecretIsNull",
    "LicenseNotFound", "InvalidLicense", "LicenseIsExpire",
    "FilenameIsNull", "TargetFilenameIsNull", "CallbackIsNull",
    "ConflictSourceInfo", "SourceInfoIsNull", "FileUrlNotAllowed",
    "CallbackUrlNotAllowed", "DocTypeNotSupport", "InvalidArgument",
    "ParamTypeError", "ParamOutOfRange", "NotSupportOptions",
}

RES_CODES_SERVER = {
    "ServerBusy", "TaskQueueCongestion", "NotSupportTask",
    "UnknownErr", "DownloadErr", "TaskTimeout",
}


def classify_openapi_failure(task):
    errors_text = " ".join(task.error_messages).lower()

    if task.convert_err_code and task.convert_err_code in CONVERT_ERR_CODES:
        return CONVERT_ERR_CODES[task.convert_err_code]

    if task.res_code:
        if task.res_code in RES_CODES_CLIENT:
            return f"客户端参数/鉴权错误 ({task.res_code})"
        if task.res_code in RES_CODES_SERVER:
            return f"服务端错误 ({task.res_code})"

    if "outofmemoryerror" in errors_text or "heap space" in errors_text:
        return "JVM 堆内存溢出 (OOM)"
    if "timeout" in errors_text or "timed out" in errors_text:
        return "超时"
    if "download" in errors_text and ("fail" in errors_text or "error" in errors_text):
        return "源文件下载失败"
    if "mime" in errors_text:
        return "MIME 类型不匹配"
    if "password" in errors_text or "encrypt" in errors_text:
        return "加密文档问题"
    if "busy" in errors_text or "lock" in errors_text:
        return "服务繁忙/队列满"
    if "model op" in errors_text:
        return "ModelOp 执行失败"
    if "callback" in errors_text or "notify" in errors_text:
        return "回调通知失败"

    return "未知原因"


# ─── 格式化输出 ───

def _fmt_size(b):
    if b >= 1024 * 1024:
        return f"{b / 1024 / 1024:.1f}MB"
    if b >= 1024:
        return f"{b / 1024:.1f}KB"
    return f"{b}B"


def _abbreviate(msg, max_len=120):
    msg = msg.strip().replace("\n", " ")
    if len(msg) > max_len:
        return msg[:max_len] + "..."
    return msg


def print_task_detail(task, index, java_errors):
    status = "成功" if task.is_success else "失败"
    print(f"\n{'━' * 60}")
    print(f"  [{index}] {status}任务")
    print(f"  taskId:      {task.tid}")
    if task.stage:
        print(f"  失败阶段:    {task.stage}")
    if task.res_code:
        print(f"  ResCode:     {task.res_code}")
    if task.convert_err_code and task.convert_err_code != "0":
        desc = CONVERT_ERR_CODES.get(task.convert_err_code, "")
        print(f"  转换错误码:  {task.convert_err_code} {desc}")
    if task.result_code:
        print(f"  结果码:      {task.result_code}")
    if task.duration_ms:
        print(f"  耗时:        {task.duration_ms}ms")
    if task.file_size:
        print(f"  文件大小:    {_fmt_size(task.file_size)}")
    if task.download_ms:
        print(f"  下载耗时:    {task.download_ms}ms")

    if not task.is_success:
        err_summary = "; ".join(task.error_messages[:5]) if task.error_messages else "(无具体报错)"
        print(f"  报错信息:    {err_summary}")
        print(f"  原因分类:    {classify_openapi_failure(task)}")

    if task.tid in java_errors:
        print(f"  Java 错误:")
        for je in java_errors[task.tid][:3]:
            print(f"    {je}")

    if task.timeline:
        print(f"  时间线:")
        for ts, event in task.timeline:
            ts_short = ts[11:] if len(ts) > 11 else ts
            print(f"    {ts_short}  {event}")


def print_summary(tasks):
    total = len(tasks)
    success = sum(1 for t in tasks.values() if t.is_success)
    failed = total - success

    print(f"\n{'═' * 60}")
    print(f"  汇总")
    print(f"{'═' * 60}")
    print(f"  OpenAPI 任务总数:  {total}")
    print(f"  成功:              {success}")
    print(f"  失败:              {failed}")

    if failed > 0:
        reason_dist = defaultdict(int)
        stage_dist = defaultdict(int)
        for t in tasks.values():
            if not t.is_success:
                reason_dist[classify_openapi_failure(t)] += 1
                stage_dist[t.stage or "(未确定)"] += 1

        print(f"\n  失败阶段分布:")
        for stage, cnt in sorted(stage_dist.items(), key=lambda x: -x[1]):
            print(f"    {stage}: {cnt} 个")

        print(f"\n  失败原因分布:")
        for reason, cnt in sorted(reason_dist.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {cnt} 个")


# ─── 主流程 ───

def main():
    parser = argparse.ArgumentParser(description="洛书 OpenAPI 任务失败分析")
    parser.add_argument("--logDir", nargs="+", required=True, help="日志目录路径")
    parser.add_argument("--taskId", nargs="+", default=[], help="按 taskId 筛选")
    parser.add_argument("--timeFrom", default=None, help="起始时间 (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--timeTo", default=None, help="结束时间 (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--all", action="store_true", help="显示所有任务（包括成功任务）")
    args = parser.parse_args()

    ts_from = parse_ts(args.timeFrom) if args.timeFrom else None
    ts_to = parse_ts(args.timeTo) if args.timeTo else None
    task_ids = set(args.taskId)

    log_dirs = discover_log_dirs(args.logDir)
    if not log_dirs:
        print("[ERROR] 未找到有效的日志目录", file=sys.stderr)
        sys.exit(1)

    print(f"发现 {len(log_dirs)} 个日志目录:")
    for d in log_dirs:
        print(f"  {d}")

    ds_files = find_log_files(log_dirs, "NewDocServer_")
    ts_files = find_log_files(log_dirs, "TaskServer_")

    if not task_ids:
        if not ts_from:
            print("[ERROR] 未指定 --taskId 时必须指定 --timeFrom", file=sys.stderr)
            sys.exit(1)
        print(f"\n按时间段扫描 OpenAPI taskId...")
        task_ids = scan_all_openapi_tasks(ds_files, ts_files, ts_from, ts_to)
        print(f"  发现 {len(task_ids)} 个 OpenAPI 相关 taskId")

    if not task_ids:
        print("\n未找到匹配的 OpenAPI 任务。")
        return

    print(f"\n扫描 {len(ds_files)} 个 NewDocServer 日志文件...")
    ds_tasks = scan_docserver_openapi(ds_files, task_ids, ts_from, ts_to)
    print(f"  找到 {len(ds_tasks)} 个任务记录")

    print(f"扫描 {len(ts_files)} 个 TaskServer 日志文件...")
    ts_tasks = scan_taskserver_openapi(ts_files, task_ids, ts_from, ts_to)
    print(f"  找到 {len(ts_tasks)} 个任务记录")

    print("扫描 Java 日志...")
    java_errors = scan_java_logs(log_dirs, task_ids, ts_from, ts_to)
    print(f"  找到 {len(java_errors)} 个 Java 错误")

    all_tasks = {}
    for tid in task_ids:
        if tid in ds_tasks or tid in ts_tasks:
            task = ds_tasks.get(tid, OpenAPITask(tid))
            ts_task = ts_tasks.get(tid)
            if ts_task:
                task.timeline.extend(ts_task.timeline)
                task.error_messages.extend(
                    e for e in ts_task.error_messages if e not in task.error_messages
                )
                if ts_task.convert_err_code:
                    task.convert_err_code = ts_task.convert_err_code
                if ts_task.result_code:
                    task.result_code = ts_task.result_code
                if ts_task.file_size:
                    task.file_size = ts_task.file_size
                if ts_task.download_ms:
                    task.download_ms = ts_task.download_ms
                if ts_task.duration_ms:
                    task.duration_ms = ts_task.duration_ms
                if ts_task.stage and not task.stage:
                    task.stage = ts_task.stage
            task.timeline.sort(key=lambda x: x[0])
            all_tasks[tid] = task

    if not args.all:
        filtered = {tid: t for tid, t in all_tasks.items() if not t.is_success}
    else:
        filtered = all_tasks

    if not filtered:
        print("\n未找到失败任务。" if not args.all else "\n未找到匹配的任务。")
        print_summary(all_tasks)
        return

    sorted_tasks = sorted(filtered.values(),
                          key=lambda t: t.timeline[0][0] if t.timeline else "")

    failed_tasks = [t for t in sorted_tasks if not t.is_success]
    success_tasks = [t for t in sorted_tasks if t.is_success]

    if failed_tasks:
        print(f"\n{'═' * 60}")
        print(f"  失败任务详情 ({len(failed_tasks)} 个)")
        print(f"{'═' * 60}")
        for i, task in enumerate(failed_tasks, 1):
            print_task_detail(task, i, java_errors)

    if success_tasks and args.all:
        print(f"\n{'═' * 60}")
        print(f"  成功任务摘要 ({len(success_tasks)} 个)")
        print(f"{'═' * 60}")
        for i, task in enumerate(success_tasks, 1):
            print_task_detail(task, i, java_errors)

    print_summary(all_tasks)


if __name__ == "__main__":
    main()
