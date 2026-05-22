"""
Archive extraction utility for RCA platform.

Extracts log files (.log, .txt, .out, .err, .csv) from uploaded
archive files (.zip, .tar.gz, .tgz, .tar), concatenates their
content, and returns a single string for further processing.
"""

from __future__ import annotations

import logging
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

LOG_EXTENSIONS = {'.log', '.txt', '.out', '.err', '.csv', '.conf', '.cfg', '.ini', '.xml', '.json', '.yaml', '.yml'}
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100MB safety limit
MAX_FILES = 500


def extract_logs_from_archive(archive_path: str, original_filename: str = '') -> str:
    """Extract text-based log files from an archive and concatenate content.

    Returns concatenated text of all log-like files found, each prefixed
    with a ``=== filename ===`` header.  Returns empty string if no
    suitable files are found.
    """
    archive_path = str(archive_path)
    original_filename = original_filename.lower()

    if zipfile.is_zipfile(archive_path):
        return _extract_from_zip(archive_path)
    elif tarfile.is_tarfile(archive_path):
        return _extract_from_tar(archive_path)
    elif original_filename.endswith('.gz') and not original_filename.endswith('.tar.gz'):
        return _extract_from_gzip_single(archive_path)
    else:
        log.warning('Unsupported archive format: %s', archive_path)
        return ''


def _is_log_file(name: str) -> bool:
    """Check if a filename looks like a log or text-based config file."""
    ext = Path(name).suffix.lower()
    return ext in LOG_EXTENSIONS


def _extract_from_zip(archive_path: str) -> str:
    parts: list[str] = []
    total_size = 0
    file_count = 0

    try:
        with zipfile.ZipFile(archive_path, 'r') as zf:
            for info in sorted(zf.infolist(), key=lambda i: i.filename):
                if info.is_dir():
                    continue
                if not _is_log_file(info.filename):
                    continue
                if info.file_size > MAX_TOTAL_SIZE:
                    log.warning('Skipping oversized file in zip: %s (%d bytes)', info.filename, info.file_size)
                    continue

                total_size += info.file_size
                if total_size > MAX_TOTAL_SIZE:
                    parts.append(f'\n=== [TRUNCATED: total size limit {MAX_TOTAL_SIZE // 1024 // 1024}MB reached] ===\n')
                    break

                file_count += 1
                if file_count > MAX_FILES:
                    parts.append(f'\n=== [TRUNCATED: max {MAX_FILES} files reached] ===\n')
                    break

                try:
                    content = zf.read(info.filename).decode('utf-8', errors='replace')
                    parts.append(f'=== {info.filename} ===\n{content}')
                except Exception as e:
                    log.warning('Failed to read %s from zip: %s', info.filename, e)
    except Exception as e:
        log.exception('Failed to process zip archive: %s', e)

    return '\n\n'.join(parts)


def _extract_from_tar(archive_path: str) -> str:
    parts: list[str] = []
    total_size = 0
    file_count = 0

    try:
        with tarfile.open(archive_path, 'r:*') as tf:
            for member in sorted(tf.getmembers(), key=lambda m: m.name):
                if not member.isfile():
                    continue
                if not _is_log_file(member.name):
                    continue
                if member.size > MAX_TOTAL_SIZE:
                    log.warning('Skipping oversized file in tar: %s (%d bytes)', member.name, member.size)
                    continue

                total_size += member.size
                if total_size > MAX_TOTAL_SIZE:
                    parts.append(f'\n=== [TRUNCATED: total size limit {MAX_TOTAL_SIZE // 1024 // 1024}MB reached] ===\n')
                    break

                file_count += 1
                if file_count > MAX_FILES:
                    parts.append(f'\n=== [TRUNCATED: max {MAX_FILES} files reached] ===\n')
                    break

                try:
                    f = tf.extractfile(member)
                    if f:
                        content = f.read().decode('utf-8', errors='replace')
                        parts.append(f'=== {member.name} ===\n{content}')
                except Exception as e:
                    log.warning('Failed to read %s from tar: %s', member.name, e)
    except Exception as e:
        log.exception('Failed to process tar archive: %s', e)

    return '\n\n'.join(parts)


def _extract_from_gzip_single(archive_path: str) -> str:
    """Handle .gz files that are not tar archives (single compressed file)."""
    import gzip

    try:
        with gzip.open(archive_path, 'rt', encoding='utf-8', errors='replace') as f:
            content = f.read(MAX_TOTAL_SIZE)
            return content
    except Exception as e:
        log.warning('Failed to decompress gzip file: %s', e)
        return ''
