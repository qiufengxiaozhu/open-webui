"""
Skills Gate — semantic routing for RCA platform.

Loads Skill documents from ``SKILLS_DIR`` (default ``docs/skills/``),
each with YAML front-matter.  All Skill descriptions and full content
are injected into the system prompt on every LLM call; the LLM
autonomously determines whether the user's question relates to a
registered Skill.  ``keywords`` in front-matter is optional and no
longer used for gating decisions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from open_webui.env import ENABLE_SKILLS_GATE, SKILLS_DIR

log = logging.getLogger(__name__)

_FRONT_MATTER_RE = re.compile(r'\A---\s*\n(.*?)\n---\s*\n', re.DOTALL)


@dataclass
class SkillDoc:
    name: str
    description: str
    content: str
    file_path: str
    keywords: list[str] = field(default_factory=list)


class SkillsGate:
    """Singleton that caches Skill documents and builds semantic-routing prompts."""

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
                    log.info('Loaded skill: %s', skill.name)
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

        body = raw[m.end():]
        return SkillDoc(
            name=name,
            keywords=keywords,
            description=meta.get('description', ''),
            content=body.strip(),
            file_path=file_path,
        )

    # 系统提示词模板文件路径（相对于 SKILLS_DIR）
    _PROMPT_TEMPLATE_FILE = 'system-prompt.md'

    def _load_prompt_template(self) -> str:
        """从 SKILLS_DIR/system-prompt.md 加载系统提示词模板。

        模板使用 YAML front-matter 头部（会被剥离），正文中可包含
        ``{{skill_names}}`` 和 ``{{skills_xml}}`` 两个占位符。
        如果文件不存在或读取失败，返回内置的最简回退模板。
        """
        prompt_file = (SKILLS_DIR or Path('docs/skills')) / self._PROMPT_TEMPLATE_FILE
        try:
            raw = prompt_file.read_text(encoding='utf-8')
            # 剥离 YAML front-matter
            m = _FRONT_MATTER_RE.match(raw)
            if m:
                raw = raw[m.end():]
            return raw.strip()
        except FileNotFoundError:
            log.warning('系统提示词模板文件不存在: %s，使用内置回退模板', prompt_file)
        except Exception:
            log.exception('读取系统提示词模板失败: %s，使用内置回退模板', prompt_file)

        return (
            '你是专业运维故障诊断助手（RCA - Root Cause Analysis）。\n\n'
            '## 已注册技能文档\n\n'
            '<skills_context>\n{{skills_xml}}\n</skills_context>\n\n'
            '已支持领域：[{{skill_names}}]'
        )

    def build_full_system_prompt(self) -> str:
        """Build the full system prompt with role, all Skills, and domain constraints.

        从 system-prompt.md 模板文件加载提示词，替换 {{skill_names}} 和
        {{skills_xml}} 占位符后返回。修改提示词只需编辑该文件，无需改代码。
        """
        if not self._loaded:
            self.load()

        skill_names = ', '.join(s.name for s in self.skills) or '（暂无）'

        parts = []
        for s in self.skills:
            parts.append(
                f'<skill name="{s.name}" description="{s.description}">\n'
                f'{s.content}\n</skill>'
            )
        skills_xml = '\n'.join(parts)

        template = self._load_prompt_template()
        return template.replace('{{skill_names}}', skill_names).replace('{{skills_xml}}', skills_xml)


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
