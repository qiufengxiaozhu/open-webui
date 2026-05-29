"""
RCA 平台的压缩包解压工具。

从上传的压缩包文件（.zip, .tar.gz, .tgz, .tar）中提取日志文件
（.log, .txt, .out, .err, .csv），拼接内容后返回字符串；
也支持将文件解压到磁盘目录供工具直接访问。
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
    """从压缩包中提取日志文件并拼接内容。

    返回所有日志类文件的拼接文本，每个文件前带 ``=== filename ===`` 头。
    未找到合适文件时返回空字符串。
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
    """判断文件名是否为日志或文本配置文件。"""
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


def extract_archive_to_dir(archive_path: str, target_dir: str, original_filename: str = '') -> list[str]:
    """将压缩包中的日志文件解压到 *target_dir* 磁盘目录。

    返回解压后的文件路径列表（相对于 *target_dir*）。
    仅提取扩展名在 LOG_EXTENSIONS 内的文件。
    """
    archive_path = str(archive_path)
    original_filename = original_filename.lower()
    os.makedirs(target_dir, exist_ok=True)
    extracted: list[str] = []

    try:
        if zipfile.is_zipfile(archive_path):
            extracted = _extract_zip_to_dir(archive_path, target_dir)
        elif tarfile.is_tarfile(archive_path):
            extracted = _extract_tar_to_dir(archive_path, target_dir)
        elif original_filename.endswith('.gz') and not original_filename.endswith('.tar.gz'):
            import gzip
            out_name = Path(original_filename).stem or 'decompressed.log'
            out_path = os.path.join(target_dir, out_name)
            with gzip.open(archive_path, 'rb') as f_in, open(out_path, 'wb') as f_out:
                while True:
                    chunk = f_in.read(1024 * 1024)
                    if not chunk:
                        break
                    f_out.write(chunk)
            extracted = [out_name]
        else:
            log.warning('Unsupported archive format for disk extraction: %s', archive_path)
    except Exception as e:
        log.exception('Failed to extract archive to dir: %s', e)

    if extracted:
        total_size = sum(
            os.path.getsize(os.path.join(target_dir, f))
            for f in extracted
            if os.path.isfile(os.path.join(target_dir, f))
        )
        log.info(f'[RCA:archive] 解压完成 files={len(extracted)} total_size={total_size}B dir={target_dir}')

    return extracted


def _extract_zip_to_dir(archive_path: str, target_dir: str) -> list[str]:
    extracted: list[str] = []
    total_size = 0
    with zipfile.ZipFile(archive_path, 'r') as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir() or not _is_log_file(info.filename):
                continue
            if info.file_size > MAX_TOTAL_SIZE:
                continue
            total_size += info.file_size
            if total_size > MAX_TOTAL_SIZE or len(extracted) >= MAX_FILES:
                break
            safe_name = info.filename.replace('..', '_').lstrip('/')
            out_path = os.path.join(target_dir, safe_name)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with zf.open(info.filename) as src, open(out_path, 'wb') as dst:
                dst.write(src.read())
            extracted.append(safe_name)
    return extracted


def _extract_tar_to_dir(archive_path: str, target_dir: str) -> list[str]:
    extracted: list[str] = []
    total_size = 0
    with tarfile.open(archive_path, 'r:*') as tf:
        for member in sorted(tf.getmembers(), key=lambda m: m.name):
            if not member.isfile() or not _is_log_file(member.name):
                continue
            if member.size > MAX_TOTAL_SIZE:
                continue
            total_size += member.size
            if total_size > MAX_TOTAL_SIZE or len(extracted) >= MAX_FILES:
                break
            safe_name = member.name.replace('..', '_').lstrip('/')
            out_path = os.path.join(target_dir, safe_name)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            f = tf.extractfile(member)
            if f:
                with open(out_path, 'wb') as dst:
                    dst.write(f.read())
                extracted.append(safe_name)
    return extracted


def _extract_from_gzip_single(archive_path: str) -> str:
    """处理非 tar 的单独 .gz 压缩文件。"""
    import gzip

    try:
        with gzip.open(archive_path, 'rt', encoding='utf-8', errors='replace') as f:
            content = f.read(MAX_TOTAL_SIZE)
            return content
    except Exception as e:
        log.warning('Failed to decompress gzip file: %s', e)
        return ''


# ── 解压目录自动过期清理 ──────────────────────────────────────────

EXTRACTED_TTL_DAYS = int(os.getenv('EXTRACTED_TTL_DAYS', '7'))


def cleanup_expired_extracted_dirs(upload_dir: str, ttl_days: int = EXTRACTED_TTL_DAYS) -> int:
    """清理超过 TTL 的解压目录，返回清理的目录数量。"""
    import shutil
    import time

    extracted_root = os.path.join(upload_dir, 'extracted')
    if not os.path.isdir(extracted_root):
        return 0

    now = time.time()
    ttl_seconds = ttl_days * 86400
    cleaned = 0

    for entry in os.listdir(extracted_root):
        dir_path = os.path.join(extracted_root, entry)
        if not os.path.isdir(dir_path):
            continue
        try:
            mtime = os.path.getmtime(dir_path)
            if now - mtime > ttl_seconds:
                shutil.rmtree(dir_path, ignore_errors=True)
                cleaned += 1
                log.info('已清理过期解压目录: %s（已存在 %d 天）', dir_path, int((now - mtime) / 86400))
        except Exception as e:
            log.warning('清理解压目录失败 %s: %s', dir_path, e)

    return cleaned


async def periodic_extracted_cleanup(upload_dir: str, interval_hours: int = 24):
    """定时清理过期解压目录的后台任务。每 interval_hours 小时执行一次。"""
    import asyncio

    while True:
        try:
            cleaned = await asyncio.to_thread(cleanup_expired_extracted_dirs, upload_dir)
            if cleaned > 0:
                log.info('定时清理完成，共清理 %d 个过期解压目录', cleaned)
        except Exception as e:
            log.warning('定时清理解压目录出错: %s', e)
        await asyncio.sleep(interval_hours * 3600)
