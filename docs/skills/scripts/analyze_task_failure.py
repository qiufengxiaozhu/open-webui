#!/usr/bin/env python3
"""
洛书任务失败分析脚本。

扫描 TaskServer / NewDocServer 日志，定位失败任务并输出：
taskId、error code、报错信息、原因分类、完整时间线。

成功判定：Result error code 1299 = 成功，其余为失败。

用法:
    python3 analyze_task_failure.py --logDir /path/to/logs --docId <docId>
    python3 analyze_task_failure.py --logDir /path/to/logs --taskId <taskId>
    python3 analyze_task_failure.py --logDir /path/to/logs --timeFrom "2026-05-22 14:00:00" --timeTo "2026-05-22 15:00:00"
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S.%f"
SUCCESS_CODE = "1299"

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


def find_log_files(log_dirs, component_prefix):
    files = []
    for d in log_dirs:
        for comp_dir in sorted(d.iterdir()):
            if comp_dir.is_dir() and comp_dir.name.startswith(component_prefix):
                for f in sorted(comp_dir.glob("combined-*.log*")):
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
    """读取日志文件，自动拼接跨行 JSON 条目（message 含真实换行符时）。"""
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
                    # 非 JSON 开头且无缓冲，跳过孤立行
            if buf:
                entries.append("".join(buf))
    except OSError as e:
        print(f"[WARN] 读取文件失败 {fpath}: {e}", file=sys.stderr)
    return entries


def parse_multiline_entry(entry):
    """解析可能跨行的日志条目，兜底用正则提取 timestamp/message/level。"""
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


# ─── 正则模式 ───

RE_RECEIVE_TASK = re.compile(
    r"\[Task Server\]\[\d+\] Receive Task : \[([0-9a-f-]+) / (\w+)\]"
)

RE_RESULT = re.compile(
    r"\[ConvertWorker\] Result of Conversion task \[([0-9a-f-]+)\]:\s+\[(\d+)\]"
)

RE_CONVERT_REQUEST = re.compile(
    r"\[LocalConvertServiceImp\] Get convert request of task \[([0-9a-f-]+)\]"
)

RE_DONE_EXECUTION = re.compile(
    r"\[LocalConvertServiceImp\] Done the execution of conversion task "
    r"\[([0-9a-f-]+)\] \[([^\]]*)\] in (\d+)ms with code \[(\d+)\]"
)

RE_DOWNLOAD_DONE = re.compile(
    r"\[LocalConvertServiceImp\] Download of task \[([0-9a-f-]+)\] \[([^\]]*)\].*?done in (\d+)ms"
)

RE_FILESIZE = re.compile(
    r"\[LocalConvertServiceImp\] tid:([0-9a-f-]+) fileSize is (\d+) B"
)

RE_PDF_MERGE_FAILED = re.compile(
    r"\[PDFMergeConvertor\] Convert task \[([0-9a-f-]+)\] is failed in \[(\d+)\]ms on error (.*)",
    re.DOTALL
)

RE_CL_EXIT_CODE = re.compile(
    r"\[CLConvertor\] ExitCode \(code=(\d+);signal=([^;]*);errCode:(\d+);id=([0-9a-f-]+)\[([^\]]*)\]\)"
)

RE_CL_ERROR = re.compile(
    r"\[CLConvertor\] error spawnAsync\(id=([0-9a-f-]+)\[([^\]]*)\]\)"
)

RE_CL_STDERR = re.compile(
    r"\[CLConvertor\] stderr \(id=([0-9a-f-]+)\[([^\]]*)\]\):(.*)"
)

RE_DOWNLOAD_FAILED = re.compile(
    r"\[ConvertFileService\].*is failed to returned as source stream in (\d+)ms"
)

RE_COMPARE_DOWNLOAD_FAILED = re.compile(
    r"\[TextCompareConvertor\] Compare task \[([0-9a-f-]+)\] is failed with another file download failed"
)

RE_COMPLETED = re.compile(
    r"\[ConvertWorker\] Conversion task \[([0-9a-f-]+)\] is completed in (\d+)ms"
)

RE_MODEL_WORKER_START = re.compile(
    r"\[ModelWorker\] Message task is starting \[([0-9a-f-]+)\] \(([^,]+),\s*([^)]+)\)"
)

RE_MODEL_WORKER_DONE = re.compile(
    r"\[ModelWorker\] Message task \[([0-9a-f-]+)\] is completed in (\d+)ms"
)

RE_EXPORT_WORKER_DONE = re.compile(
    r"\[ExportDocWorker\].*task \[([0-9a-f-]+)\].*is completed in (\d+)\s*ms"
)

RE_POST_RESPONSE = re.compile(
    r"\[Task Server\]\[\d+\] \[([0-9a-f-]+)\] Post Task Response : \[[0-9a-f-]+ / (\w+)\]"
)

RE_TASK_STARTED = re.compile(
    r"(\w+) task \[([0-9a-f-]+)\] is started"
)

# NewDocServer 侧
RE_DS_CONVERSION_FAILED = re.compile(
    r"\[ConversionService\] Conversion task of.*?docId.*?([0-9a-f]{20,}).*?is failed for error \[(\d+)\]"
)

RE_DS_GET_RESPONSE = re.compile(
    r"\[ConversionService\] Get Response of ([0-9a-f]+) from conversion server.*?code.*?'(\d+)'"
)

RE_DS_COMPARE_START = re.compile(
    r"\[compareDocByAspose\] start compare docA:([0-9a-f]+),docB:([0-9a-f]+)"
)

RE_DS_COMPARE_ERR = re.compile(
    r"\[compareDocErr\].*?compare doc error.*?docA.*?([0-9a-f]{20,}).*?docB.*?([0-9a-f]{20,})"
)

# viewType / taskCtx 提取
RE_VIEW_TYPE = re.compile(r"viewType:\s*'(\w+)'")
RE_META_TYPE = re.compile(r"metaType:\s*'(\w+)'")
RE_TASK_CTX_DOCA = re.compile(r"docA:\s*'([0-9a-f]+)'")
RE_TASK_CTX_DOCB = re.compile(r"docB:\s*'([0-9a-f]+)'")
RE_DOC_KEY = re.compile(r"docId.*?['\"]([0-9a-f]{20,})['\"]")


# ─── 任务数据结构 ───

class TaskInfo:
    __slots__ = (
        "tid", "task_type", "doc_id", "result_code", "done_code",
        "duration_ms", "file_size", "download_ms", "view_type",
        "meta_type", "error_messages", "timeline", "cl_exit_code",
        "cl_err_code", "cl_stderr", "response_type", "doc_a", "doc_b",
        "_request_block",
    )

    def __init__(self, tid, task_type=""):
        self.tid = tid
        self.task_type = task_type
        self.doc_id = ""
        self.result_code = ""
        self.done_code = ""
        self.duration_ms = 0
        self.file_size = 0
        self.download_ms = 0
        self.view_type = ""
        self.meta_type = ""
        self.error_messages = []
        self.timeline = []
        self.cl_exit_code = ""
        self.cl_err_code = ""
        self.cl_stderr = ""
        self.response_type = ""
        self.doc_a = ""
        self.doc_b = ""
        self._request_block = ""

    @property
    def is_success(self):
        return self.result_code == SUCCESS_CODE

    @property
    def display_type(self):
        t = self.task_type
        if self.view_type:
            t += f" ({self.view_type})"
        elif self.meta_type:
            t += f" ({self.meta_type})"
        return t

    def add_timeline(self, ts, event):
        self.timeline.append((ts, event))

    def add_error(self, msg):
        self.error_messages.append(msg)


# ─── TaskServer 日志扫描 ───

def scan_taskserver_logs(log_files, doc_ids, task_ids, ts_from, ts_to):
    tasks = {}
    pending_request_tid = None
    pending_request_lines = []

    for fpath in log_files:
        pending_request_tid = None
        pending_request_lines = []
        entries = read_log_entries(fpath)

        for entry in entries:
            parsed = parse_multiline_entry(entry)
            if parsed is None:
                if pending_request_tid:
                    pending_request_lines.append(entry.strip())
                continue

            ts_str, msg, level = parsed

            if pending_request_tid:
                if entry.strip().startswith('{"timestamp"'):
                    _flush_request_block(pending_request_tid, pending_request_lines, tasks, doc_ids, task_ids)
                    pending_request_tid = None
                    pending_request_lines = []
                else:
                    pending_request_lines.append(msg)
                    continue

            if not ts_in_range(ts_str, ts_from, ts_to):
                m = RE_RECEIVE_TASK.search(msg)
                if m:
                    tid = m.group(1)
                    if tid in tasks:
                        del tasks[tid]
                continue

            _process_taskserver_line(ts_str, msg, level, tasks, doc_ids, task_ids,
                                    pending_request_lines)
            m = RE_CONVERT_REQUEST.search(msg)
            if m:
                pending_request_tid = m.group(1)
                pending_request_lines = [msg]

    if pending_request_tid:
        _flush_request_block(pending_request_tid, pending_request_lines, tasks, doc_ids, task_ids)

    return tasks


def _flush_request_block(tid, lines, tasks, doc_ids, task_ids):
    block = "\n".join(lines)
    task = tasks.get(tid)
    if task:
        task._request_block = block
        _extract_request_info(task, block)
        if not _matches_filter(task, doc_ids, task_ids):
            del tasks[tid]


def _extract_request_info(task, block):
    m = RE_VIEW_TYPE.search(block)
    if m:
        task.view_type = m.group(1)
    m = RE_META_TYPE.search(block)
    if m:
        task.meta_type = m.group(1)
    m = RE_TASK_CTX_DOCA.search(block)
    if m:
        task.doc_a = m.group(1)
    m = RE_TASK_CTX_DOCB.search(block)
    if m:
        task.doc_b = m.group(1)
    if not task.doc_id:
        m = RE_DOC_KEY.search(block)
        if m:
            task.doc_id = m.group(1)


def _matches_filter(task, doc_ids, task_ids):
    if not doc_ids and not task_ids:
        return True
    if task_ids and task.tid in task_ids:
        return True
    if doc_ids:
        if task.doc_id in doc_ids:
            return True
        if task.doc_a in doc_ids or task.doc_b in doc_ids:
            return True
    return False


def _ensure_task(tasks, tid, task_type, doc_ids, task_ids):
    if tid not in tasks:
        tasks[tid] = TaskInfo(tid, task_type)
    elif task_type and not tasks[tid].task_type:
        tasks[tid].task_type = task_type
    return tasks[tid]


def _process_taskserver_line(ts_str, msg, level, tasks, doc_ids, task_ids, pending_lines):
    # 任务接收
    m = RE_RECEIVE_TASK.search(msg)
    if m:
        tid, ttype = m.group(1), m.group(2)
        task = _ensure_task(tasks, tid, ttype, doc_ids, task_ids)
        task.add_timeline(ts_str, f"任务接收 [{ttype}]")
        return

    # 任务启动
    m = RE_TASK_STARTED.search(msg)
    if m:
        ttype, tid = m.group(1), m.group(2)
        if tid in tasks:
            tasks[tid].add_timeline(ts_str, f"任务启动 ({ttype})")
        return

    # 转换请求详情
    m = RE_CONVERT_REQUEST.search(msg)
    if m:
        tid = m.group(1)
        if tid in tasks:
            tasks[tid].add_timeline(ts_str, "获取转换请求详情")
        return

    # 下载完成
    m = RE_DOWNLOAD_DONE.search(msg)
    if m:
        tid, doc_id, dl_ms = m.group(1), m.group(2), int(m.group(3))
        if tid in tasks:
            task = tasks[tid]
            task.doc_id = doc_id
            task.download_ms = dl_ms
            task.add_timeline(ts_str, f"下载完成 ({dl_ms}ms)")
            if not _matches_filter(task, doc_ids, task_ids):
                del tasks[tid]
        return

    # 文件大小（exportDoc 任务的 fileSize 为中间数据大小，不具备参考意义，跳过）
    m = RE_FILESIZE.search(msg)
    if m:
        tid, size = m.group(1), int(m.group(2))
        if tid in tasks and tasks[tid].task_type != "exportDoc":
            tasks[tid].file_size = size
            tasks[tid].add_timeline(ts_str, f"文件大小 {_fmt_size(size)}")
        return

    # Done execution
    m = RE_DONE_EXECUTION.search(msg)
    if m:
        tid, doc_id, ms, code = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        if tid in tasks:
            task = tasks[tid]
            task.done_code = code
            if doc_id and not task.doc_id:
                task.doc_id = doc_id
            task.add_timeline(ts_str, f"执行完成 code={code} ({ms}ms)")
        return

    # Result
    m = RE_RESULT.search(msg)
    if m:
        tid, code = m.group(1), m.group(2)
        if tid in tasks:
            task = tasks[tid]
            task.result_code = code
            task.add_timeline(ts_str, f"任务结果 code={code}")
            if not _matches_filter(task, doc_ids, task_ids):
                del tasks[tid]
        return

    # Completed (duration)
    m = RE_COMPLETED.search(msg)
    if m:
        tid, ms = m.group(1), int(m.group(2))
        if tid in tasks:
            tasks[tid].duration_ms = ms
        return

    # PDFMergeConvertor 失败
    m = RE_PDF_MERGE_FAILED.search(msg)
    if m:
        tid, ms, err = m.group(1), m.group(2), m.group(3).strip()
        if tid in tasks:
            task = tasks[tid]
            task.add_error(f"[PDFMergeConvertor] {err}")
            task.add_timeline(ts_str, f"PDFMergeConvertor 失败 ({ms}ms)")
        return

    # CLConvertor ExitCode
    m = RE_CL_EXIT_CODE.search(msg)
    if m:
        code, signal, err_code, tid, doc_id = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        if tid in tasks:
            task = tasks[tid]
            task.cl_exit_code = code
            task.cl_err_code = err_code
            if doc_id and not task.doc_id:
                task.doc_id = doc_id
            task.add_timeline(ts_str, f"CLConvertor ExitCode code={code} errCode={err_code}")
        return

    # CLConvertor error
    m = RE_CL_ERROR.search(msg)
    if m:
        tid, doc_id = m.group(1), m.group(2)
        if tid in tasks:
            err_detail = msg.split("\n")[1] if "\n" in msg else ""
            tasks[tid].add_error(f"[CLConvertor] {err_detail.strip()}")
            tasks[tid].add_timeline(ts_str, "CLConvertor 进程异常退出")
        return

    # CLConvertor stderr
    m = RE_CL_STDERR.search(msg)
    if m:
        tid, doc_id, stderr = m.group(1), m.group(2), m.group(3).strip()
        if tid in tasks:
            tasks[tid].cl_stderr = stderr
        return

    # 下载失败
    m = RE_DOWNLOAD_FAILED.search(msg)
    if m:
        for tid, task in list(tasks.items()):
            if not task.result_code:
                task.add_error(f"[ConvertFileService] 源文件下载失败 ({m.group(1)}ms)")
                task.add_timeline(ts_str, "源文件下载失败")
                break
        return

    # 对比文件下载失败
    m = RE_COMPARE_DOWNLOAD_FAILED.search(msg)
    if m:
        tid = m.group(1)
        if tid in tasks:
            tasks[tid].add_error("[TextCompareConvertor] 对比文件下载失败")
            tasks[tid].add_timeline(ts_str, "对比文件 (docB) 下载失败")
        return

    # ModelWorker 开始
    m = RE_MODEL_WORKER_START.search(msg)
    if m:
        tid, doc_id = m.group(1), m.group(2)
        if tid in tasks:
            task = tasks[tid]
            if doc_id and not task.doc_id:
                task.doc_id = doc_id
            task.add_timeline(ts_str, f"ModelWorker 开始 ({doc_id})")
        return

    # ModelWorker 完成
    m = RE_MODEL_WORKER_DONE.search(msg)
    if m:
        tid, ms = m.group(1), int(m.group(2))
        if tid in tasks:
            task = tasks[tid]
            task.duration_ms = ms
            task.add_timeline(ts_str, f"ModelWorker 完成 ({ms}ms)")
        return

    # ExportDocWorker 完成
    m = RE_EXPORT_WORKER_DONE.search(msg)
    if m:
        tid, ms = m.group(1), int(m.group(2))
        if tid in tasks:
            task = tasks[tid]
            task.add_timeline(ts_str, f"ExportDocWorker 完成 ({ms}ms)")
        return

    # Post Task Response
    m = RE_POST_RESPONSE.search(msg)
    if m:
        tid, resp_type = m.group(1), m.group(2)
        if tid in tasks:
            tasks[tid].response_type = resp_type
            tasks[tid].add_timeline(ts_str, f"发送响应 [{resp_type}]")
        return


# ─── NewDocServer 日志扫描 ───

def scan_docserver_logs(log_files, doc_ids, task_ids, ts_from, ts_to):
    events = []
    for fpath in log_files:
        entries = read_log_entries(fpath)
        for entry in entries:
            parsed = parse_multiline_entry(entry)
            if parsed is None:
                continue
            ts_str, msg, level = parsed
            if not ts_in_range(ts_str, ts_from, ts_to):
                continue
            _process_docserver_line(ts_str, msg, level, events, doc_ids)
    return events


def _process_docserver_line(ts_str, msg, level, events, doc_ids):
    # 转换失败回调
    m = RE_DS_CONVERSION_FAILED.search(msg)
    if m:
        doc_id, code = m.group(1), m.group(2)
        if not doc_ids or doc_id in doc_ids:
            events.append({
                "ts": ts_str, "type": "conversion_failed",
                "doc_id": doc_id, "code": code,
                "msg": msg.strip()
            })
        return

    # Get Response
    m = RE_DS_GET_RESPONSE.search(msg)
    if m:
        doc_id, code = m.group(1), m.group(2)
        if not doc_ids or doc_id in doc_ids:
            events.append({
                "ts": ts_str, "type": "get_response",
                "doc_id": doc_id, "code": code
            })
        return

    # compare 开始
    m = RE_DS_COMPARE_START.search(msg)
    if m:
        doc_a, doc_b = m.group(1), m.group(2)
        if not doc_ids or doc_a in doc_ids or doc_b in doc_ids:
            events.append({
                "ts": ts_str, "type": "compare_start",
                "doc_a": doc_a, "doc_b": doc_b
            })
        return

    # compare 失败
    m = RE_DS_COMPARE_ERR.search(msg)
    if m:
        doc_a, doc_b = m.group(1), m.group(2)
        if not doc_ids or doc_a in doc_ids or doc_b in doc_ids:
            events.append({
                "ts": ts_str, "type": "compare_error",
                "doc_a": doc_a, "doc_b": doc_b,
                "msg": msg.strip()
            })
        return


# ─── 失败原因分类 ───

def classify_failure(task):
    errors_text = " ".join(task.error_messages).lower()
    stderr = (task.cl_stderr or "").lower()
    all_text = errors_text + " " + stderr

    if "outofmemoryerror" in all_text or "heap space" in all_text:
        return "JVM 堆内存溢出 (OOM)"
    if "download failed" in all_text or "下载失败" in all_text or "failed to returned as source stream" in all_text:
        return "源文件下载失败"
    if "timeout" in all_text or "timed out" in all_text or "etimedout" in all_text:
        return "任务超时"
    if "pdfmergeconvertor" in all_text:
        return "PDF 合并/对比转换失败"
    if task.cl_exit_code and task.cl_exit_code != "0":
        if "parser error" in stderr or "document is empty" in stderr:
            return "CL 转换器: 文档内容解析异常"
        return f"CL 转换器异常退出 (code={task.cl_exit_code})"
    if task.done_code and task.done_code != "200":
        return f"转换内部错误 (done_code={task.done_code})"
    if task.result_code and task.result_code != SUCCESS_CODE:
        return f"任务失败 (result_code={task.result_code})"
    return "未知原因"


# ─── 格式化输出 ───

def _fmt_size(b):
    if b >= 1024 * 1024:
        return f"{b / 1024 / 1024:.1f}MB"
    if b >= 1024:
        return f"{b / 1024:.1f}KB"
    return f"{b}B"


def print_task_detail(task, index):
    status = "成功" if task.is_success else "失败"
    print(f"\n{'━' * 60}")
    print(f"  [{index}] {status}任务")
    print(f"  taskId:      {task.tid}")
    print(f"  taskType:    {task.display_type}")
    if task.doc_id:
        print(f"  docId:       {task.doc_id}")
    if task.doc_a:
        print(f"  docA:        {task.doc_a}")
    if task.doc_b:
        print(f"  docB:        {task.doc_b}")
    print(f"  error code:  {task.result_code or '(无)'}")
    if task.duration_ms:
        print(f"  耗时:        {task.duration_ms}ms")
    if task.file_size and task.task_type != "exportDoc":
        print(f"  文件大小:    {_fmt_size(task.file_size)}")
    if task.download_ms:
        print(f"  下载耗时:    {task.download_ms}ms")

    if not task.is_success:
        err_summary = "; ".join(task.error_messages) if task.error_messages else "(无具体报错)"
        print(f"  报错信息:    {err_summary}")
        print(f"  原因分类:    {classify_failure(task)}")

    if task.cl_stderr:
        print(f"  CL stderr:   {task.cl_stderr[:200]}")

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
    print(f"  扫描任务总数:  {total}")
    print(f"  成功 (1299):   {success}")
    print(f"  失败:          {failed}")

    if failed > 0:
        code_dist = defaultdict(int)
        reason_dist = defaultdict(int)
        for t in tasks.values():
            if not t.is_success:
                code_dist[t.result_code or "(无)"] += 1
                reason_dist[classify_failure(t)] += 1

        print(f"\n  失败码分布:")
        for code, cnt in sorted(code_dist.items(), key=lambda x: -x[1]):
            print(f"    code={code}: {cnt} 个")

        print(f"\n  失败原因分布:")
        for reason, cnt in sorted(reason_dist.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {cnt} 个")

    # 按任务类型统计
    type_dist = defaultdict(lambda: {"success": 0, "failed": 0})
    for t in tasks.values():
        key = t.task_type or "(未知)"
        if t.is_success:
            type_dist[key]["success"] += 1
        else:
            type_dist[key]["failed"] += 1

    if type_dist:
        print(f"\n  任务类型分布:")
        for ttype, counts in sorted(type_dist.items()):
            s, f = counts["success"], counts["failed"]
            print(f"    {ttype}: {s + f} 个 (成功 {s}, 失败 {f})")


def print_docserver_events(events):
    if not events:
        return
    print(f"\n{'═' * 60}")
    print(f"  NewDocServer 侧相关事件")
    print(f"{'═' * 60}")
    for ev in events:
        ts = ev["ts"]
        ts_short = ts[11:] if len(ts) > 11 else ts
        if ev["type"] == "conversion_failed":
            print(f"  {ts_short}  [转换失败] docId={ev['doc_id']} code={ev['code']}")
        elif ev["type"] == "get_response":
            print(f"  {ts_short}  [收到响应] docId={ev['doc_id']} code={ev['code']}")
        elif ev["type"] == "compare_start":
            print(f"  {ts_short}  [对比开始] docA={ev['doc_a']} docB={ev['doc_b']}")
        elif ev["type"] == "compare_error":
            print(f"  {ts_short}  [对比失败] docA={ev['doc_a']} docB={ev['doc_b']}")


# ─── 过滤 ───

def filter_tasks(tasks, doc_ids, task_ids, show_all):
    if not doc_ids and not task_ids:
        if show_all:
            return tasks
        return {tid: t for tid, t in tasks.items() if not t.is_success}

    filtered = {}
    for tid, task in tasks.items():
        match = False
        if task_ids and tid in task_ids:
            match = True
        if doc_ids:
            if task.doc_id in doc_ids or task.doc_a in doc_ids or task.doc_b in doc_ids:
                match = True
        if match:
            if show_all or not task.is_success:
                filtered[tid] = task
    return filtered


# ─── 主流程 ───

def main():
    parser = argparse.ArgumentParser(description="洛书任务失败分析")
    parser.add_argument("--logDir", nargs="+", required=True, help="日志目录路径")
    parser.add_argument("--docId", nargs="+", default=[], help="按 docId 筛选")
    parser.add_argument("--taskId", nargs="+", default=[], help="按 taskId 筛选")
    parser.add_argument("--timeFrom", default=None, help="起始时间 (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--timeTo", default=None, help="结束时间 (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--all", action="store_true", help="显示所有任务（包括成功任务）")
    args = parser.parse_args()

    ts_from = parse_ts(args.timeFrom) if args.timeFrom else None
    ts_to = parse_ts(args.timeTo) if args.timeTo else None
    doc_ids = set(args.docId)
    task_ids = set(args.taskId)

    log_dirs = discover_log_dirs(args.logDir)
    if not log_dirs:
        print("[ERROR] 未找到有效的日志目录", file=sys.stderr)
        sys.exit(1)

    print(f"发现 {len(log_dirs)} 个日志目录:")
    for d in log_dirs:
        print(f"  {d}")

    # 扫描 TaskServer
    ts_files = find_log_files(log_dirs, "TaskServer_")
    print(f"\n扫描 {len(ts_files)} 个 TaskServer 日志文件...")
    all_tasks = scan_taskserver_logs(ts_files, doc_ids, task_ids, ts_from, ts_to)
    print(f"  找到 {len(all_tasks)} 个任务")

    # 从 TaskServer 结果中补充 docId 集合（taskId 搜索时自动关联）
    ds_doc_ids = set(doc_ids)
    if task_ids:
        for task in all_tasks.values():
            if task.doc_id:
                ds_doc_ids.add(task.doc_id)
            if task.doc_a:
                ds_doc_ids.add(task.doc_a)
            if task.doc_b:
                ds_doc_ids.add(task.doc_b)

    # 扫描 NewDocServer
    ds_files = find_log_files(log_dirs, "NewDocServer_")
    print(f"扫描 {len(ds_files)} 个 NewDocServer 日志文件...")
    ds_events = scan_docserver_logs(ds_files, ds_doc_ids, task_ids, ts_from, ts_to)
    print(f"  找到 {len(ds_events)} 个相关事件")

    # 过滤
    filtered = filter_tasks(all_tasks, doc_ids, task_ids, args.all)

    if not filtered:
        if doc_ids or task_ids:
            print("\n未找到匹配的任务。")
        else:
            print("\n未找到失败任务。")
        if ds_events:
            print_docserver_events(ds_events)
        return

    # 按时间排序输出
    sorted_tasks = sorted(filtered.values(),
                          key=lambda t: t.timeline[0][0] if t.timeline else "")

    failed_tasks = [t for t in sorted_tasks if not t.is_success]
    success_tasks = [t for t in sorted_tasks if t.is_success]

    if failed_tasks:
        print(f"\n{'═' * 60}")
        print(f"  失败任务详情 ({len(failed_tasks)} 个)")
        print(f"{'═' * 60}")
        for i, task in enumerate(failed_tasks, 1):
            print_task_detail(task, i)

    if success_tasks and args.all:
        print(f"\n{'═' * 60}")
        print(f"  成功任务摘要 ({len(success_tasks)} 个)")
        print(f"{'═' * 60}")
        for i, task in enumerate(success_tasks, 1):
            print_task_detail(task, i)

    print_docserver_events(ds_events)
    print_summary(all_tasks if (not doc_ids and not task_ids) else filtered)


if __name__ == "__main__":
    main()
