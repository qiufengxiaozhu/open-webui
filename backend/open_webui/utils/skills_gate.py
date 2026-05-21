"""
Skills Gate — keyword-based pre-filter for RCA platform.

Loads Skill documents from ``SKILLS_DIR`` (default ``docs/skills/``),
each with YAML front-matter containing ``keywords``.  Before every LLM
call the gate checks the user message against all keyword lists:

* **No match** → reject the request (the LLM is not called).
* **Match**    → inject matching Skill content into the system message
  so the LLM answers grounded in the Skill document.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from open_webui.env import ENABLE_SKILLS_GATE, SKILLS_DIR, SKILLS_GATE_REJECT_MESSAGE

log = logging.getLogger(__name__)

_FRONT_MATTER_RE = re.compile(r'\A---\s*\n(.*?)\n---\s*\n', re.DOTALL)


@dataclass
class SkillDoc:
    name: str
    keywords: list[str]
    description: str
    content: str
    file_path: str
    keywords_lower: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self):
        self.keywords = [str(kw) for kw in self.keywords]
        self.keywords_lower = [kw.lower() for kw in self.keywords]


class SkillsGate:
    """Singleton that caches Skill documents and performs keyword matching."""

    def __init__(self):
        self.skills: list[SkillDoc] = []
        self._loaded = False

    def load(self, skills_dir: Optional[Path] = None):
        """Scan *skills_dir* for ``.md`` files and parse them."""
        skills_dir = skills_dir or SKILLS_DIR
        self.skills.clear()

        if not skills_dir.exists():
            log.warning('Skills directory does not exist: %s', skills_dir)
            self._loaded = True
            return

        for md_file in sorted(skills_dir.glob('*.md')):
            try:
                raw = md_file.read_text(encoding='utf-8')
                skill = self._parse_skill_file(raw, str(md_file))
                if skill:
                    self.skills.append(skill)
                    log.info('Loaded skill: %s (%d keywords)', skill.name, len(skill.keywords))
            except Exception:
                log.exception('Failed to parse skill file: %s', md_file)

        self._loaded = True
        log.info('Skills gate loaded %d skill(s) from %s', len(self.skills), skills_dir)

    def reload(self, skills_dir: Optional[Path] = None):
        """Re-scan the skills directory (hot-reload support)."""
        self.load(skills_dir)

    @staticmethod
    def _parse_skill_file(raw: str, file_path: str) -> Optional[SkillDoc]:
        m = _FRONT_MATTER_RE.match(raw)
        if not m:
            log.warning('Skill file has no YAML front-matter, skipping: %s', file_path)
            return None

        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            log.exception('Invalid YAML front-matter in %s', file_path)
            return None

        name = meta.get('name', Path(file_path).stem)
        keywords = meta.get('keywords', [])
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(',') if k.strip()]
        if not keywords:
            log.warning('Skill file has no keywords, skipping: %s', file_path)
            return None

        body = raw[m.end():]
        return SkillDoc(
            name=name,
            keywords=keywords,
            description=meta.get('description', ''),
            content=body.strip(),
            file_path=file_path,
        )

    def match_skills(self, message: str) -> list[SkillDoc]:
        """Return all Skills whose keywords appear in *message*."""
        if not self._loaded:
            self.load()
        msg_lower = message.lower()
        return [s for s in self.skills if any(kw in msg_lower for kw in s.keywords_lower)]

    def build_reject_message(self) -> str:
        """Build the rejection text shown when no Skill matches."""
        if SKILLS_GATE_REJECT_MESSAGE:
            return SKILLS_GATE_REJECT_MESSAGE

        skill_names = ', '.join(s.name for s in self.skills) or '（暂无技能文档）'
        return (
            f'抱歉，您的问题与现有的技能文档不相关，无法回答。\n\n'
            f'当前可用的技能文档：{skill_names}\n\n'
            f'请提出与以上技能文档相关的问题。'
        )

    def build_system_injection(self, matched: list[SkillDoc]) -> str:
        """Build the system-message fragment with matched Skill content."""
        parts = []
        for s in matched:
            parts.append(f'<skill name="{s.name}">\n{s.content}\n</skill>')
        skills_xml = '\n'.join(parts)
        return (
            f'<skills_context>\n{skills_xml}\n</skills_context>\n\n'
            '请基于以上技能文档回答用户的问题。如果文档中没有相关信息，请明确告知。'
        )


# ── module-level singleton ──────────────────────────────────────────

_gate = SkillsGate()


def init(skills_dir: Optional[Path] = None):
    """Called once at application startup."""
    if ENABLE_SKILLS_GATE:
        _gate.load(skills_dir)
    else:
        log.info('Skills gate is disabled (ENABLE_SKILLS_GATE=false)')


def get_gate() -> SkillsGate:
    return _gate
