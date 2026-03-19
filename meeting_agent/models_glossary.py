"""
术语表数据模型
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TermType(str, Enum):
    """术语类型"""
    PERSON = "person"           # 人名
    PRODUCT = "product"         # 产品名
    TECHNICAL = "technical"     # 技术术语
    PROJECT = "project"         # 项目特定术语
    ABBREVIATION = "abbr"       # 缩写
    OTHER = "other"             # 其他


class GlossaryEntry(BaseModel):
    """术语条目"""
    canonical: str                          # 标准名称
    aliases: list[str] = Field(default_factory=list)  # 别名/常见错误识别
    type: TermType = TermType.OTHER
    description: Optional[str] = None       # 简短描述
    auto_generated: bool = True             # 是否自动生成
    confirmed_at: Optional[datetime] = None # 确认时间
    confirmed_by: Optional[str] = None      # 确认人
    source_meeting: Optional[str] = None    # 来源会议


class RejectedTerm(BaseModel):
    """被拒绝的术语"""
    canonical: str
    aliases: list[str] = Field(default_factory=list)
    type: TermType = TermType.OTHER
    context: Optional[str] = None
    source_meeting: Optional[str] = None
    reason: Optional[str] = None
    rejected_at: datetime
    rejected_by: Optional[str] = None


class Glossary(BaseModel):
    """术语表"""
    version: int = 1
    last_updated: Optional[datetime] = None
    entries: list[GlossaryEntry] = Field(default_factory=list)

    def _find_entry(self, canonical: str) -> tuple[int, GlossaryEntry] | None:
        """查找术语条目，返回 (索引, 条目) 或 None"""
        for i, entry in enumerate(self.entries):
            if entry.canonical.lower() == canonical.lower():
                return (i, entry)
        return None

    def add_entry(
        self,
        canonical: str,
        aliases: Optional[list[str]] = None,
        type: TermType = TermType.OTHER,
        description: Optional[str] = None,
        source_meeting: Optional[str] = None,
    ) -> GlossaryEntry:
        """添加术语条目"""
        entry = GlossaryEntry(
            canonical=canonical,
            aliases=aliases or [],
            type=type,
            description=description,
            auto_generated=True,
            source_meeting=source_meeting,
        )
        self.entries.append(entry)
        self.last_updated = datetime.now()
        return entry

    def get_entry(self, canonical: str) -> Optional[GlossaryEntry]:
        """获取术语条目"""
        result = self._find_entry(canonical)
        return result[1] if result else None

    def confirm(self, canonical: str) -> bool:
        """确认术语"""
        result = self._find_entry(canonical)
        if result:
            result[1].confirmed_at = datetime.now()
            self.last_updated = datetime.now()
            return True
        return False

    def get_all_aliases(self) -> dict[str, str]:
        """获取所有别名到标准名称的映射"""
        mapping = {}
        for entry in self.entries:
            for alias in entry.aliases:
                mapping[alias.lower()] = entry.canonical
            mapping[entry.canonical.lower()] = entry.canonical
        return mapping

    def to_prompt_text(self) -> str:
        """生成用于 LLM prompt 的文本"""
        if not self.entries:
            return ""

        lines = ["## 术语表（用于修正识别错误）\n"]

        # 按类型分组
        by_type: dict[TermType, list[GlossaryEntry]] = {}
        for entry in self.entries:
            if entry.confirmed_at:  # 只包含已确认的
                if entry.type not in by_type:
                    by_type[entry.type] = []
                by_type[entry.type].append(entry)

        type_names = {
            TermType.PERSON: "人名",
            TermType.PRODUCT: "产品名",
            TermType.TECHNICAL: "技术术语",
            TermType.PROJECT: "项目术语",
            TermType.ABBREVIATION: "缩写",
            TermType.OTHER: "其他",
        }

        for term_type, entries in by_type.items():
            lines.append(f"### {type_names.get(term_type, '其他')}")
            for entry in entries:
                if entry.aliases:
                    lines.append(f"- **{entry.canonical}**: 别名/常见错误 → {', '.join(entry.aliases)}")
                else:
                    lines.append(f"- **{entry.canonical}**")
            lines.append("")

        return "\n".join(lines)


class RejectedGlossary(BaseModel):
    """被拒绝的术语表（黑名单）"""
    version: int = 1
    last_updated: Optional[datetime] = None
    rejected: list[RejectedTerm] = Field(default_factory=list)

    def add(
        self,
        canonical: str,
        aliases: Optional[list[str]] = None,
        type: TermType = TermType.OTHER,
        context: Optional[str] = None,
        source_meeting: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> RejectedTerm:
        """添加拒绝的术语"""
        rejected_term = RejectedTerm(
            canonical=canonical,
            aliases=aliases or [],
            type=type,
            context=context,
            source_meeting=source_meeting,
            reason=reason,
            rejected_at=datetime.now(),
        )
        self.rejected.append(rejected_term)
        self.last_updated = datetime.now()
        return rejected_term

    def is_rejected(self, canonical: str) -> bool:
        """检查术语是否被拒绝"""
        return canonical.lower() in {r.canonical.lower() for r in self.rejected}


class TermSuggestion(BaseModel):
    """术语建议（待审核）"""
    canonical: str
    aliases: list[str] = Field(default_factory=list)
    type: TermType = TermType.OTHER
    context: Optional[str] = None        # 出现的上下文
    frequency: int = 1                   # 出现频率
    source_meeting: Optional[str] = None
    suggested_at: datetime = Field(default_factory=datetime.now)


class PendingTerms(BaseModel):
    """待审核的术语列表"""
    version: int = 1
    last_updated: Optional[datetime] = None
    suggestions: list[TermSuggestion] = Field(default_factory=list)

    def add(self, suggestion: TermSuggestion) -> None:
        """添加建议"""
        # 检查是否已存在
        for existing in self.suggestions:
            if existing.canonical.lower() == suggestion.canonical.lower():
                # 合并别名
                for alias in suggestion.aliases:
                    if alias.lower() not in [a.lower() for a in existing.aliases]:
                        existing.aliases.append(alias)
                existing.frequency += 1
                self.last_updated = datetime.now()
                return

        self.suggestions.append(suggestion)
        self.last_updated = datetime.now()

    def remove(self, canonical: str) -> bool:
        """移除建议"""
        for i, s in enumerate(self.suggestions):
            if s.canonical.lower() == canonical.lower():
                self.suggestions.pop(i)
                self.last_updated = datetime.now()
                return True
        return False
