"""
术语表管理模块
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from meeting_agent.config import Config
from meeting_agent.models_glossary import (
    Glossary,
    GlossaryEntry,
    RejectedGlossary,
    RejectedTerm,
    TermSuggestion,
    PendingTerms,
    TermType,
)

logger = logging.getLogger("meeting_agent.glossary")


class GlossaryManager:
    """术语表管理器"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self._glossary: Optional[Glossary] = None
        self._rejected: Optional[RejectedGlossary] = None
        self._pending: Optional[PendingTerms] = None

    @property
    def glossary_file(self) -> Path:
        return self.config.meetings_dir / "_glossary.json"

    @property
    def rejected_file(self) -> Path:
        return self.config.meetings_dir / "_glossary_rejected.json"

    @property
    def pending_file(self) -> Path:
        return self.config.meetings_dir / "_glossary_pending.json"

    def load_glossary(self) -> Glossary:
        """加载术语表"""
        if self._glossary is not None:
            return self._glossary

        if self.glossary_file.exists():
            try:
                with open(self.glossary_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._glossary = Glossary(**data)
                logger.info("加载术语表: %d 条", len(self._glossary.entries))
            except Exception as e:
                logger.warning("加载术语表失败: %s", e)
                self._glossary = Glossary()
        else:
            self._glossary = Glossary()

        return self._glossary

    def save_glossary(self) -> None:
        """保存术语表"""
        if self._glossary is None:
            return

        self._glossary.last_updated = datetime.now()

        with open(self.glossary_file, "w", encoding="utf-8") as f:
            json.dump(self._glossary.model_dump(mode="json"), f, ensure_ascii=False, indent=2)

        logger.info("保存术语表: %s", self.glossary_file)

    def load_rejected(self) -> RejectedGlossary:
        """加载拒绝表"""
        if self._rejected is not None:
            return self._rejected

        if self.rejected_file.exists():
            try:
                with open(self.rejected_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._rejected = RejectedGlossary(**data)
                logger.info("加载拒绝表: %d 条", len(self._rejected.rejected))
            except Exception as e:
                logger.warning("加载拒绝表失败: %s", e)
                self._rejected = RejectedGlossary()
        else:
            self._rejected = RejectedGlossary()

        return self._rejected

    def save_rejected(self) -> None:
        """保存拒绝表"""
        if self._rejected is None:
            return

        with open(self.rejected_file, "w", encoding="utf-8") as f:
            json.dump(self._rejected.model_dump(mode="json"), f, ensure_ascii=False, indent=2)

        logger.info("保存拒绝表: %s", self.rejected_file)

    def load_pending(self) -> PendingTerms:
        """加载待审核术语"""
        if self._pending is not None:
            return self._pending

        if self.pending_file.exists():
            try:
                with open(self.pending_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._pending = PendingTerms(**data)
                logger.info("加载待审核术语: %d 条", len(self._pending.suggestions))
            except Exception as e:
                logger.warning("加载待审核术语失败: %s", e)
                self._pending = PendingTerms()
        else:
            self._pending = PendingTerms()

        return self._pending

    def save_pending(self) -> None:
        """保存待审核术语"""
        if self._pending is None:
            return

        with open(self.pending_file, "w", encoding="utf-8") as f:
            json.dump(self._pending.model_dump(mode="json"), f, ensure_ascii=False, indent=2)

        logger.info("保存待审核术语: %s", self.pending_file)

    def get_term(self, canonical: str) -> Optional[GlossaryEntry]:
        """获取术语"""
        glossary = self.load_glossary()
        return glossary.get_entry(canonical)

    def add_term(
        self,
        canonical: str,
        aliases: Optional[list[str]] = None,
        type: TermType = TermType.OTHER,
        auto_generated: bool = True,
    ) -> GlossaryEntry:
        """添加术语"""
        glossary = self.load_glossary()
        entry = glossary.add_entry(
            canonical=canonical,
            aliases=aliases or [],
            type=type,
        )
        self.save_glossary()
        return entry

    def remove_entry(self, canonical: str) -> bool:
        """从术语表中删除术语"""
        glossary = self.load_glossary()
        result = glossary._find_entry(canonical)
        if result:
            glossary.entries.pop(result[0])
            glossary.last_updated = datetime.now()
            self.save_glossary()
            return True
        return False

    def update_entry(
        self,
        canonical: str,
        new_canonical: Optional[str] = None,
        aliases: Optional[list[str]] = None,
        type: Optional[TermType] = None,
        context: Optional[str] = None,
    ) -> Optional[GlossaryEntry]:
        """更新术语表中的术语"""
        glossary = self.load_glossary()
        result = glossary._find_entry(canonical)
        if not result:
            return None
        idx, entry = result
        if new_canonical is not None:
            entry.canonical = new_canonical
        if aliases is not None:
            entry.aliases = aliases
        if type is not None:
            entry.type = type
        if context is not None:
            entry.context = context
        glossary.last_updated = datetime.now()
        glossary.entries[idx] = entry
        self.save_glossary()
        return entry

    def revert_confirmed_to_pending(self, canonical: str) -> bool:
        """将已确认术语回退到待审核状态"""
        glossary = self.load_glossary()
        result = glossary._find_entry(canonical)
        if not result:
            return False
        idx, entry = result
        pending = self.load_pending()
        from meeting_agent.models_glossary import TermSuggestion
        suggestion = TermSuggestion(
            canonical=entry.canonical,
            aliases=entry.aliases,
            type=entry.type,
            context=entry.context or entry.description,
            source_meeting=entry.source_meeting,
        )
        pending.add(suggestion)
        self.save_pending()
        glossary.entries.pop(idx)
        glossary.last_updated = datetime.now()
        self.save_glossary()
        return True

    def revert_rejected_to_pending(self, canonical: str) -> bool:
        """将已拒绝术语回退到待审核状态"""
        rejected = self.load_rejected()
        term = next((r for r in rejected.rejected if r.canonical.lower() == canonical.lower()), None)
        if not term:
            return False
        pending = self.load_pending()
        from meeting_agent.models_glossary import TermSuggestion
        suggestion = TermSuggestion(
            canonical=term.canonical,
            aliases=term.aliases,
            type=term.type,
            context=term.context,
            source_meeting=term.source_meeting,
        )
        pending.add(suggestion)
        self.save_pending()
        rejected.rejected = [r for r in rejected.rejected if r.canonical.lower() != canonical.lower()]
        rejected.last_updated = datetime.now()
        self.save_rejected()
        return True

    def confirm_term(self, canonical: str) -> bool:
        """确认术语"""
        glossary = self.load_glossary()
        entry = glossary.get_entry(canonical)
        if entry:
            entry.confirmed_at = datetime.now()
            self.save_glossary()
            return True
        return False

    def reject_term(
        self,
        canonical: str,
        aliases: Optional[list[str]] = None,
        type: TermType = TermType.OTHER,
        context: Optional[str] = None,
        source_meeting: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> RejectedTerm:
        """拒绝术语"""
        rejected = self.load_rejected()
        result = rejected.add(
            canonical=canonical,
            aliases=aliases,
            type=type,
            context=context,
            source_meeting=source_meeting,
            reason=reason,
        )
        self.save_rejected()

        # 从待审核中移除
        pending = self.load_pending()
        pending.remove(canonical)
        self.save_pending()

        return result

    def suggest_term(
        self,
        canonical: str,
        aliases: Optional[list[str]] = None,
        term_type: TermType = TermType.OTHER,
        context: Optional[str] = None,
        source_meeting: Optional[str] = None,
    ) -> Optional[TermSuggestion]:
        """添加术语建议"""
        # 检查是否已在已接受术语表中
        glossary = self.load_glossary()
        if glossary.get_entry(canonical):
            logger.debug("术语已在术语表中，跳过: %s", canonical)
            return None

        # 检查是否已被拒绝
        rejected = self.load_rejected()
        if rejected.is_rejected(canonical):
            logger.debug("术语已被拒绝，跳过: %s", canonical)
            return None

        pending = self.load_pending()
        suggestion = TermSuggestion(
            canonical=canonical,
            aliases=aliases or [],
            type=term_type,
            context=context,
            source_meeting=source_meeting,
        )
        pending.add(suggestion)
        self.save_pending()

        return suggestion

    def approve_suggestion(self, canonical: str) -> Optional[GlossaryEntry]:
        """批准建议的术语"""
        pending = self.load_pending()
        for suggestion in pending.suggestions:
            if suggestion.canonical.lower() == canonical.lower():
                # 添加到术语表
                entry = self.add_term(
                    canonical=suggestion.canonical,
                    aliases=suggestion.aliases,
                    type=suggestion.type,
                    auto_generated=True,
                )
                # 从待审核中移除
                pending.remove(canonical)
                self.save_pending()
                return entry
        return None

    def reject_suggestion(self, canonical: str, reason: Optional[str] = None) -> bool:
        """拒绝建议的术语"""
        pending = self.load_pending()
        # 先找到建议，获取完整信息
        suggestion = None
        for s in pending.suggestions:
            if s.canonical.lower() == canonical.lower():
                suggestion = s
                break

        if suggestion:
            pending.remove(canonical)
            self.reject_term(
                canonical=suggestion.canonical,
                aliases=suggestion.aliases,
                type=suggestion.type,
                context=suggestion.context,
                source_meeting=suggestion.source_meeting,
                reason=reason,
            )
            self.save_pending()
            return True
        return False

    def build_correction_map(self) -> dict[str, str]:
        """
        构建纠错映射
        key: 错误/别名 -> value: 正确形式
        """
        glossary = self.load_glossary()
        correction_map = {}

        for entry in glossary.entries:
            for alias in entry.aliases:
                correction_map[alias.lower()] = entry.canonical

        return correction_map

    def build_glossary_prompt(self) -> str:
        """构建用于 LLM 的术语表 prompt"""
        glossary = self.load_glossary()

        if not glossary.entries:
            return ""

        # 按类型分组
        by_type: dict[TermType, list[GlossaryEntry]] = {}
        for entry in glossary.entries:
            if entry.type not in by_type:
                by_type[entry.type] = []
            by_type[entry.type].append(entry)

        type_names = {
            TermType.PERSON: "人员",
            TermType.PRODUCT: "产品/项目",
            TermType.TECHNICAL: "技术术语",
            TermType.PROJECT: "项目术语",
            TermType.ABBREVIATION: "缩写",
            TermType.OTHER: "其他",
        }

        lines = ["## 术语表（用于修正识别错误）\n"]

        for term_type, entries in by_type.items():
            lines.append(f"### {type_names.get(term_type, '其他')}")
            for entry in entries:
                if entry.aliases:
                    lines.append(f"- **{entry.canonical}**: 常见错误 → {', '.join(entry.aliases)}")
                else:
                    lines.append(f"- **{entry.canonical}**")
            lines.append("")

        return "\n".join(lines)
