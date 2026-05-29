import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from open_webui.env import (
    ENABLE_AUDIT_STDOUT,
    ENABLE_AUDIT_LOGS_FILE,
    AUDIT_LOGS_FILE_PATH,
    AUDIT_LOG_FILE_ROTATION_SIZE,
    AUDIT_LOG_LEVEL,
    GLOBAL_LOG_LEVEL,
    LOG_FORMAT,
    AUDIT_UVICORN_LOGGER_NAMES,
    ENABLE_OTEL,
    ENABLE_OTEL_LOGS,
    ENABLE_APP_LOG_FILE,
    APP_LOG_DIR,
    APP_LOG_RETENTION,
    APP_LOG_ARCHIVE_MONTHS,
    _LEVEL_MAP,
)

if TYPE_CHECKING:
    from loguru import Message, Record


def stdout_format(record: 'Record') -> str:
    """
    Generates a formatted string for log records that are output to the console. This format includes a timestamp, log level, source location (module, function, and line), the log message, and any extra data (serialized as JSON).

    Parameters:
    record (Record): A Loguru record that contains logging details including time, level, name, function, line, message, and any extra context.
    Returns:
    str: A formatted log string intended for stdout.
    """
    if record['extra']:
        record['extra']['extra_json'] = json.dumps(record['extra'])
        extra_format = ' - {extra[extra_json]}'
    else:
        extra_format = ''
    return (
        '<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | '
        '<level>{level: <8}</level> | '
        '<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - '
        '<level>{message}</level>' + extra_format + '\n{exception}'
    )


def _plain_format(record: 'Record') -> str:
    """文件日志格式（无 ANSI 颜色码）。"""
    if record['extra']:
        record['extra']['extra_json'] = json.dumps(record['extra'])
        extra_format = ' - {extra[extra_json]}'
    else:
        extra_format = ''
    return (
        '{time:YYYY-MM-DD HH:mm:ss.SSS} | '
        '{level: <8} | '
        '{name}:{function}:{line} - '
        '{message}' + extra_format + '\n{exception}'
    )


def _json_sink(message: 'Message') -> None:
    """Write log records as single-line JSON to stdout.

    Used as a Loguru sink when LOG_FORMAT is set to "json".
    """
    record = message.record
    log_entry = {
        'ts': record['time'].strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
        'level': _LEVEL_MAP.get(record['level'].name, record['level'].name.lower()),
        'msg': record['message'],
        'caller': f'{record["name"]}:{record["function"]}:{record["line"]}',
    }

    if record['extra']:
        log_entry['extra'] = record['extra']

    if record['exception'] is not None:
        log_entry['error'] = ''.join(record['exception'].format_exception()).rstrip()

    sys.stdout.write(json.dumps(log_entry, ensure_ascii=False, default=str) + '\n')
    sys.stdout.flush()


class InterceptHandler(logging.Handler):
    """
    Intercepts log records from Python's standard logging module
    and redirects them to Loguru's logger.
    """

    def emit(self, record):
        """
        Called by the standard logging module for each log event.
        It transforms the standard `LogRecord` into a format compatible with Loguru
        and passes it to Loguru's logger.
        """
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).bind(**self._get_extras()).log(level, record.getMessage())
        if ENABLE_OTEL and ENABLE_OTEL_LOGS:
            from open_webui.utils.telemetry.logs import otel_handler

            otel_handler.emit(record)

    def _get_extras(self):
        if not ENABLE_OTEL:
            return {}

        from opentelemetry import trace

        extras = {}
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            extras['trace_id'] = trace.format_trace_id(context.trace_id)
            extras['span_id'] = trace.format_span_id(context.span_id)
        return extras


def file_format(record: 'Record'):
    """
    Formats audit log records into a structured JSON string for file output.

    Parameters:
    record (Record): A Loguru record containing extra audit data.
    Returns:
    str: A JSON-formatted string representing the audit data.
    """

    audit_data = {
        'id': record['extra'].get('id', ''),
        'timestamp': int(record['time'].timestamp()),
        'user': record['extra'].get('user', dict()),
        'audit_level': record['extra'].get('audit_level', ''),
        'verb': record['extra'].get('verb', ''),
        'request_uri': record['extra'].get('request_uri', ''),
        'response_status_code': record['extra'].get('response_status_code', 0),
        'source_ip': record['extra'].get('source_ip', ''),
        'user_agent': record['extra'].get('user_agent', ''),
        'request_object': record['extra'].get('request_object', b''),
        'response_object': record['extra'].get('response_object', b''),
        'extra': record['extra'].get('extra', {}),
    }

    record['extra']['file_extra'] = json.dumps(audit_data, default=str)
    return '{extra[file_extra]}\n'


def _archive_old_logs_by_month(log_dir: Path, archive_months: int = 1):
    """将超过 archive_months 个月前的 .log 轮转文件按月打成 .tar.gz 归档。

    归档文件命名格式: app-2026-04.tar.gz
    已归档的 .log 文件会被删除。
    """
    import tarfile
    from collections import defaultdict

    if archive_months < 1:
        return

    now = datetime.now()
    cutoff_year = now.year
    cutoff_month = now.month - archive_months
    while cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year -= 1

    # loguru 轮转文件格式: app.2026-05-28_00-00-00_000000.log
    rotated_files = defaultdict(list)
    for f in log_dir.glob('app.*.log'):
        stem = f.stem  # e.g. "app.2026-05-28_00-00-00_000000"
        parts = stem.split('.')
        if len(parts) < 2:
            continue
        date_part = parts[1].split('_')[0]  # "2026-05-28"
        try:
            file_date = datetime.strptime(date_part, '%Y-%m-%d')
        except ValueError:
            continue

        if (file_date.year, file_date.month) < (cutoff_year, cutoff_month):
            month_key = file_date.strftime('%Y-%m')
            rotated_files[month_key].append(f)

    for month_key, files in rotated_files.items():
        archive_path = log_dir / f'app-{month_key}.tar.gz'
        if archive_path.exists():
            for f in files:
                f.unlink(missing_ok=True)
            continue

        try:
            with tarfile.open(archive_path, 'w:gz') as tar:
                for f in sorted(files):
                    tar.add(f, arcname=f.name)
            for f in files:
                f.unlink(missing_ok=True)
            logger.info(f'Archived {len(files)} log files to {archive_path}')
        except Exception as e:
            logger.error(f'Failed to archive logs for {month_key}: {e}')


def start_logger():
    """
    Initializes and configures Loguru's logger with distinct handlers:

    A console (stdout) handler for general log messages (excluding those marked as auditable).
    An optional file handler for application logs with daily rotation.
    An optional file handler for audit logs if audit logging is enabled.
    Additionally, this function reconfigures Python's standard logging to route through Loguru and adjusts logging levels for Uvicorn.
    """
    logger.remove()

    audit_filter = lambda record: True if ENABLE_AUDIT_STDOUT else 'auditable' not in record['extra']
    if LOG_FORMAT == 'json':
        logger.add(
            _json_sink,
            level=GLOBAL_LOG_LEVEL,
            filter=audit_filter,
        )
    else:
        logger.add(
            sys.stdout,
            level=GLOBAL_LOG_LEVEL,
            format=stdout_format,
            filter=audit_filter,
        )

    # 应用日志文件 — 每日午夜轮转
    if ENABLE_APP_LOG_FILE:
        try:
            APP_LOG_DIR.mkdir(parents=True, exist_ok=True)
            app_log_path = APP_LOG_DIR / 'app.log'
            logger.add(
                str(app_log_path),
                level=GLOBAL_LOG_LEVEL,
                format=_plain_format,
                rotation='00:00',
                retention=APP_LOG_RETENTION,
                encoding='utf-8',
                enqueue=True,
                filter=lambda record: 'auditable' not in record['extra'],
            )
            logger.info(f'App log file: {app_log_path} (daily rotation, retention={APP_LOG_RETENTION})')
            _archive_old_logs_by_month(APP_LOG_DIR, APP_LOG_ARCHIVE_MONTHS)
        except Exception as e:
            logger.error(f'Failed to initialize app log file handler: {e}')

    # 审计日志文件 — 按大小轮转
    if AUDIT_LOG_LEVEL != 'NONE' and ENABLE_AUDIT_LOGS_FILE:
        try:
            logger.add(
                AUDIT_LOGS_FILE_PATH,
                level='INFO',
                rotation=AUDIT_LOG_FILE_ROTATION_SIZE,
                compression='zip',
                format=file_format,
                filter=lambda record: record['extra'].get('auditable') is True,
            )
        except Exception as e:
            logger.error(f'Failed to initialize audit log file handler: {str(e)}')

    logging.basicConfig(handlers=[InterceptHandler()], level=GLOBAL_LOG_LEVEL, force=True)

    for uvicorn_logger_name in ['uvicorn', 'uvicorn.error']:
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.setLevel(GLOBAL_LOG_LEVEL)
        uvicorn_logger.handlers = []

    for uvicorn_logger_name in AUDIT_UVICORN_LOGGER_NAMES:
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.setLevel(GLOBAL_LOG_LEVEL)
        uvicorn_logger.handlers = [InterceptHandler()]

    logger.info(f'GLOBAL_LOG_LEVEL: {GLOBAL_LOG_LEVEL}')
