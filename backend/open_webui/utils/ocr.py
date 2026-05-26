"""
OCR utility — extract text from images.

Uses pytesseract (Tesseract) when available.  Falls back gracefully
if neither is installed: the caller simply receives an empty string.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def extract_text_from_image(file_path: str) -> str:
    """Return OCR-extracted text from *file_path*, or ``""`` on failure.

    Priority:
      1. pytesseract + Pillow (needs ``tesseract`` binary on PATH)
    """
    file_path = str(file_path)

    # --- pytesseract ---
    try:
        from PIL import Image, ImageFilter
        import pytesseract

        img = Image.open(file_path)
        if img.mode != 'L':
            img = img.convert('L')

        text = pytesseract.image_to_string(img, lang='chi_sim+eng')
        text = text.strip()
        if text:
            log.info('OCR (pytesseract) extracted %d chars from %s', len(text), Path(file_path).name)
            return text
    except ImportError:
        log.debug('pytesseract / Pillow not installed — OCR unavailable')
    except Exception as exc:
        log.warning('pytesseract OCR failed for %s: %s', file_path, exc)

    return ''
