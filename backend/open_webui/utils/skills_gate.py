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

    def build_full_system_prompt(self) -> str:
        """Build the full system prompt with role, all Skills, and domain constraints."""
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

        return (
            '你是专业运维故障诊断助手（RCA - Root Cause Analysis）。\n\n'
            '## 领域约束（严格遵守）\n'
            '你仅回答与以下已注册技能文档相关的故障诊断问题。\n'
            '若用户问题不属于以下任何领域，直接回复：\n'
            f'"抱歉，当前故障类型不在系统支持范围内。已支持领域：[{skill_names}]"\n'
            '不要尝试回答超出领域的问题。\n\n'
            '## 已注册技能文档\n\n'
            f'<skills_context>\n{skills_xml}\n</skills_context>\n\n'
            '## 可用工具\n'
            '- grep_log(pattern, file?, context?): 正则搜索日志（压缩包会自动解压到磁盘，在完整原始日志中搜索）\n'
            '- get_context(file, line, before?, after?): 查看某行上下文\n'
            '- time_window(start, end, level?): 按时间范围筛选\n'
            '- count_errors(file?, top_n?): 聚合统计错误\n'
            '- list_files(): 查看可用文件（含压缩包内的子文件列表）\n'
            '- run_script(script, lang?): 执行分析脚本(bash/python3)，环境变量 LOG_DIR 指向解压后的日志目录\n\n'
            '## 文件处理说明\n'
            '- 用户上传的压缩包(.zip/.tar.gz等)会自动解压到磁盘\n'
            '- grep_log 等工具会直接在解压后的**完整原始日志**中搜索，不是裁剪版\n'
            '- run_script 沙箱中可通过环境变量 $LOG_DIR 访问解压后的日志文件目录\n\n'
            '## 分析脚本（优先使用）\n'
            '每个技能文档的末尾"脚本辅助"章节包含预置的 Python 分析脚本路径和用法。\n'
            '沙箱环境中已预载分析脚本，通过 $SCRIPTS_DIR 访问。\n'
            '分析日志时优先通过 run_script 工具调用对应的分析脚本快速定位问题，\n'
            '再结合技能文档中的链路知识做深入分析。\n'
            '调用示例（lang="bash"）：\n'
            '```\n'
            'python3 $SCRIPTS_DIR/analyze_openapi_failure.py --logDir $LOG_DIR --taskId <taskId>\n'
            'python3 $SCRIPTS_DIR/analyze_task_failure.py --logDir $LOG_DIR --docId <docId>\n'
            '```\n\n'
            '## 工作流程（严格执行）\n'
            '**核心原则：必须先搜索日志获取真实证据，再做分析。绝不能跳过工具调用直接凭摘要回答。**\n\n'
            '1. 先判断问题是否在支持领域内\n'
            '2. 用户上传了日志文件/压缩包时，先用 list_files() 确认文件列表和解压状态\n'
            '3. **【强制】用户提供了 taskId/docId 时，必须立即调用 grep_log(taskId) 在完整原始日志中搜索**\n'
            '   - 禁止仅凭文件摘要/裁剪版内容回答，摘要只用于了解全局概况\n'
            '   - grep_log 会在磁盘上的解压原始日志中搜索，能找到完整链路\n'
            '4. 根据 grep_log 返回的真实日志内容，追踪任务完整生命周期\n'
            '5. 必要时用 get_context 查看更多上下文，用 time_window 筛选时间范围\n'
            '6. 可调用 run_script 执行分析脚本做深入分析（利用 $LOG_DIR 环境变量）\n'
            '7. 基于真实日志证据输出结构化诊断报告\n\n'
            '## 输出格式\n'
            '请输出结构化 Markdown 报告：\n'
            '- 故障概况\n'
            '- 根因分析（三层根因）\n'
            '- 排查路径\n'
            '- 时间线（如有）\n'
            '- 修复建议\n'
            '- 影响范围\n'
            '- 预防措施'
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
