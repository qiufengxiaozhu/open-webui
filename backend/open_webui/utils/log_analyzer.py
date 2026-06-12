import re
from collections import Counter
from datetime import datetime
from typing import Optional

DEFAULT_MAX_TOKEN_BUDGET = 8000
CHARS_PER_TOKEN = 1.5
TOP_ERROR_PATTERNS = 20
CONTEXT_LINES = 50

LOG_LEVEL_PATTERN = re.compile(
    r'\b(ERROR|ERR|WARN|WARNING|INFO|DEBUG|FATAL|TRACE|CRITICAL|SEVERE)\b',
    re.IGNORECASE,
)

ERROR_TRIGGER_PATTERN = re.compile(
    r'\b(ERROR|ERR|FATAL|CRITICAL|SEVERE|Exception|Traceback)\b',
    re.IGNORECASE,
)

TIMESTAMP_PATTERNS = [
    re.compile(
        r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)'
    ),
    re.compile(r'\[(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]'),
    re.compile(r'(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})'),
    re.compile(r'(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2})'),
    re.compile(r'(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})'),
    # Java util.logging 格式: "May 25, 2026 2:50:16 AM" / "May 25, 2026 10:50:16 PM"
    re.compile(
        r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)',
        re.IGNORECASE,
    ),
]

STACK_LINE_PATTERN = re.compile(
    r'^(\s+|at\s+\S|\s+File\s+["\']|\s*Caused by:|\s*\.\.\.\s+\d+\s+more|\s+~\[\d+\]'
    r'|[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*\.[A-Z]\w*([Ee]xception|[Ee]rror):)',  # Java 异常类名行
    re.IGNORECASE,
)

NORMALIZE_PATTERN = re.compile(
    r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?|\d{2}:\d{2}:\d{2}(?:\.\d+)?|\b0x[0-9a-fA-F]+\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\b\d+\b',
    re.IGNORECASE,
)


def _chars_for_tokens(token_budget: int) -> int:
    return int(token_budget * CHARS_PER_TOKEN)


def _normalize_level(level: str) -> str:
    level = level.upper()
    if level in ('WARN', 'WARNING'):
        return 'WARN'
    if level in ('ERR', 'CRITICAL', 'SEVERE'):
        return 'ERROR'
    return level


def _extract_timestamp(line: str) -> Optional[str]:
    for pattern in TIMESTAMP_PATTERNS:
        match = pattern.search(line)
        if match:
            return match.group(1)
    return None


def _parse_timestamp(ts: str) -> Optional[datetime]:
    normalized = ts.replace('T', ' ').replace('Z', '').strip()
    for fmt in (
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
        '%Y/%m/%d %H:%M:%S',
        '%m-%d-%Y %H:%M:%S',
        '%m/%d/%Y %H:%M:%S',
        '%b %d, %Y %I:%M:%S %p',  # Java util.logging 格式: "May 25, 2026 2:50:16 AM"
    ):
        try:
            return datetime.strptime(normalized[:26], fmt)
        except ValueError:
            continue
    return None


def _normalize_error_pattern(line: str) -> str:
    cleaned = NORMALIZE_PATTERN.sub(' ', line)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:200] if cleaned else line.strip()[:200]


def _is_stack_continuation(line: str, prev_is_stack: bool) -> bool:
    if not line.strip():
        return prev_is_stack
    if STACK_LINE_PATTERN.match(line):
        return True
    if prev_is_stack and line.startswith((' ', '\t')):
        return True
    return False


def _is_error_line(line: str) -> bool:
    return bool(ERROR_TRIGGER_PATTERN.search(line))


def _extract_error_blocks(lines: list[str]) -> list[dict]:
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_error_line(line):
            block_lines = [line]
            j = i + 1
            prev_stack = True
            while j < len(lines) and _is_stack_continuation(lines[j], prev_stack):
                block_lines.append(lines[j])
                prev_stack = True
                j += 1
            blocks.append({'line': i + 1, 'content': '\n'.join(block_lines)})
            i = j
        else:
            i += 1
    return blocks


def _build_summary_text(
    lines: list[str],
    error_blocks: list[dict],
    top_errors: list[dict],
    max_chars: int,
) -> str:
    parts: list[str] = []
    used_lines: set[int] = set()

    for block in error_blocks:
        used_lines.update(range(block['line'], block['line'] + block['content'].count('\n') + 1))

    for err in top_errors:
        used_lines.add(err['first_line'])

    head = lines[:CONTEXT_LINES]
    tail = lines[-CONTEXT_LINES:] if len(lines) > CONTEXT_LINES else []

    sections = [
        ('--- 日志开头上下文 ---', head),
        ('--- 错误块 ---', [b['content'] for b in error_blocks]),
        ('--- 日志结尾上下文 ---', tail),
    ]

    for title, section_lines in sections:
        if not section_lines:
            continue
        chunk = title + '\n' + '\n'.join(
            item if isinstance(item, str) else item for item in section_lines
        )
        if sum(len(p) for p in parts) + len(chunk) > max_chars:
            remaining = max_chars - sum(len(p) for p in parts)
            if remaining > 100:
                parts.append(chunk[:remaining] + '\n...(truncated)')
            break
        parts.append(chunk)

    return '\n\n'.join(parts)


def parse_log_content(content: str, max_token_budget: int = DEFAULT_MAX_TOKEN_BUDGET) -> dict:
    if not content:
        return {
            'total_lines': 0,
            'time_range': {'start': None, 'end': None},
            'level_counts': {},
            'top_errors': [],
            'error_blocks': [],
            'summary_text': '',
        }

    lines = content.splitlines()
    total_lines = len(lines)
    level_counts: Counter = Counter()
    timestamps: list[tuple[int, datetime, str]] = []
    error_pattern_lines: dict[str, int] = {}
    error_pattern_counts: Counter = Counter()

    for idx, line in enumerate(lines):
        for match in LOG_LEVEL_PATTERN.finditer(line):
            level_counts[_normalize_level(match.group(1))] += 1

        ts_str = _extract_timestamp(line)
        if ts_str:
            parsed = _parse_timestamp(ts_str)
            if parsed:
                timestamps.append((idx, parsed, ts_str))

        if _is_error_line(line):
            pattern = _normalize_error_pattern(line)
            error_pattern_counts[pattern] += 1
            if pattern not in error_pattern_lines:
                error_pattern_lines[pattern] = idx + 1

    error_blocks = _extract_error_blocks(lines)

    top_errors = [
        {
            'pattern': pattern,
            'count': count,
            'first_line': error_pattern_lines[pattern],
        }
        for pattern, count in error_pattern_counts.most_common(TOP_ERROR_PATTERNS)
    ]

    if timestamps:
        timestamps.sort(key=lambda x: x[1])
        time_range = {
            'start': timestamps[0][2],
            'end': timestamps[-1][2],
        }
    else:
        time_range = {'start': None, 'end': None}

    max_chars = _chars_for_tokens(max_token_budget)
    summary_text = _build_summary_text(lines, error_blocks, top_errors, max_chars)

    return {
        'total_lines': total_lines,
        'time_range': time_range,
        'level_counts': dict(level_counts),
        'top_errors': top_errors,
        'error_blocks': error_blocks,
        'summary_text': summary_text,
    }


def _format_duration(start_ts: Optional[str], end_ts: Optional[str]) -> str:
    if not start_ts or not end_ts:
        return ''
    start = _parse_timestamp(start_ts)
    end = _parse_timestamp(end_ts)
    if not start or not end or end <= start:
        return ''
    delta = end - start
    minutes = int(delta.total_seconds() // 60)
    if minutes >= 60:
        hours, mins = divmod(minutes, 60)
        return f'（{hours} 小时 {mins} 分钟）'
    return f'（{minutes} 分钟）'


def _dedupe_error_blocks(error_blocks: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for block in error_blocks:
        key = _normalize_error_pattern(block['content'].split('\n', 1)[0])
        if key in seen:
            continue
        seen.add(key)
        unique.append(block)
    return unique


def format_log_summary_for_llm(analysis: dict) -> str:
    total_lines = analysis.get('total_lines', 0)
    time_range = analysis.get('time_range') or {}
    level_counts = analysis.get('level_counts') or {}
    top_errors = analysis.get('top_errors') or []
    error_blocks = _dedupe_error_blocks(analysis.get('error_blocks') or [])

    start = time_range.get('start') or '未知'
    end = time_range.get('end') or '未知'
    duration = _format_duration(time_range.get('start'), time_range.get('end'))

    error_count = level_counts.get('ERROR', 0)
    warn_count = level_counts.get('WARN', 0)

    lines = [
        '## 日志分析摘要',
        f'- 总行数: {total_lines:,} 行',
        f'- 时间范围: {start} ~ {end}{duration}',
        f'- 错误统计: ERROR {error_count} 条, WARN {warn_count} 条',
    ]

    if top_errors:
        lines.append('')
        lines.append('### Top 错误模式')
        for i, err in enumerate(top_errors[:10], 1):
            lines.append(
                f'{i}. `{err["pattern"]}` — 出现 {err["count"]} 次'
                f'（首次出现在第 {err["first_line"]} 行）'
            )

    if error_blocks:
        lines.append('')
        lines.append('### 关键错误详情')
        for block in error_blocks[:20]:
            lines.append(f'**第 {block["line"]} 行**')
            lines.append('```')
            lines.append(block['content'])
            lines.append('```')

    return '\n'.join(lines)
