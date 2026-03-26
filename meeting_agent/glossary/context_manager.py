"""
项目上下文管理模块

_context.json 存储结构化的 Q&A 条目，帮助 LLM 理解项目专有名词和背景。
每条 entry = {id, topic, question, answer?, source_meeting?, created_at, answered_at?}
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from meeting_agent.config import Config

logger = logging.getLogger("meeting_agent.context")


class ContextEntry(BaseModel):
    """背景说明条目（一个问题及其解答）"""
    id: str
    topic: str                              # 简短标题
    question: str                           # 完整问题描述
    answer: Optional[str] = None            # 解答（为空则未回答）
    source_meeting: Optional[str] = None    # 来源会议
    created_at: datetime = Field(default_factory=datetime.now)
    answered_at: Optional[datetime] = None

    @property
    def is_answered(self) -> bool:
        return bool(self.answer and self.answer.strip())


class ContextStore(BaseModel):
    """背景说明存储"""
    version: int = 1
    last_updated: Optional[datetime] = None
    entries: list[ContextEntry] = Field(default_factory=list)


class ContextManager:
    """项目背景说明管理器"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self._store: Optional[ContextStore] = None

    @property
    def context_file(self) -> Path:
        return self.config.meetings_dir / "_context.json"

    @property
    def legacy_md_file(self) -> Path:
        return self.config.meetings_dir / "_context.md"

    # ------------------------------------------------------------------ #
    # Load / Save
    # ------------------------------------------------------------------ #

    def load_store(self, force_reload: bool = False) -> ContextStore:
        if self._store is not None and not force_reload:
            return self._store

        if self.context_file.exists():
            try:
                with open(self.context_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._store = ContextStore(**data)
                return self._store
            except Exception as e:
                logger.warning("加载背景说明失败: %s", e)

        self._store = ContextStore()
        return self._store

    def save_store(self) -> None:
        if self._store is None:
            return
        self._store.last_updated = datetime.now()
        with open(self.context_file, "w", encoding="utf-8") as f:
            json.dump(self._store.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
        logger.info("保存背景说明: %s", self.context_file)

    # ------------------------------------------------------------------ #
    # Entry CRUD
    # ------------------------------------------------------------------ #

    def list_entries(self) -> list[ContextEntry]:
        return list(self.load_store().entries)

    def get_entry(self, entry_id: str) -> Optional[ContextEntry]:
        store = self.load_store()
        return next((e for e in store.entries if e.id == entry_id), None)

    def add_entry(
        self,
        topic: str,
        question: str,
        answer: Optional[str] = None,
        source_meeting: Optional[str] = None,
    ) -> ContextEntry:
        store = self.load_store()
        # Deduplicate by topic
        for existing in store.entries:
            if existing.topic.lower() == topic.lower():
                logger.debug("条目已存在，跳过: %s", topic)
                return existing
        entry = ContextEntry(
            id=secrets.token_hex(5),
            topic=topic,
            question=question,
            answer=answer or None,
            source_meeting=source_meeting,
            answered_at=datetime.now() if answer else None,
        )
        store.entries.append(entry)
        self.save_store()
        return entry

    def update_entry(
        self,
        entry_id: str,
        topic: Optional[str] = None,
        question: Optional[str] = None,
        answer: Optional[str] = None,
    ) -> Optional[ContextEntry]:
        store = self.load_store()
        entry = next((e for e in store.entries if e.id == entry_id), None)
        if not entry:
            return None
        if topic is not None:
            entry.topic = topic
        if question is not None:
            entry.question = question
        if answer is not None:
            was_answered = entry.is_answered
            entry.answer = answer.strip() or None
            if entry.answer and not was_answered:
                entry.answered_at = datetime.now()
            elif not entry.answer:
                entry.answered_at = None
        self.save_store()
        return entry

    def delete_entry(self, entry_id: str) -> bool:
        store = self.load_store()
        before = len(store.entries)
        store.entries = [e for e in store.entries if e.id != entry_id]
        if len(store.entries) < before:
            self.save_store()
            return True
        return False

    # ------------------------------------------------------------------ #
    # Compatibility: append_questions (called from __main__.py)
    # ------------------------------------------------------------------ #

    def append_questions(
        self,
        questions: list[dict[str, str]],
        source_meeting: Optional[str] = None,
    ) -> int:
        """追加问题列表（兼容旧调用接口）。"""
        if not questions:
            return 0
        count = 0
        store = self.load_store()
        existing_topics = {e.topic.lower() for e in store.entries}
        for q in questions:
            topic = q.get("topic", "未知主题")
            if topic.lower() in existing_topics:
                logger.debug("问题已存在，跳过: %s", topic)
                continue
            entry = ContextEntry(
                id=secrets.token_hex(5),
                topic=topic,
                question=q.get("question", ""),
                answer=None,
                source_meeting=source_meeting,
            )
            store.entries.append(entry)
            existing_topics.add(topic.lower())
            count += 1
        if count:
            self.save_store()
        logger.info("追加 %d 个待解释问题", count)
        return count

    def initialize(self) -> None:
        """初始化（新项目）：如存在旧 md 文件则迁移，否则创建空 store。"""
        if self.context_file.exists():
            return
        self._store = ContextStore()
        self.save_store()
        logger.info("初始化背景说明: %s", self.context_file)

    # ------------------------------------------------------------------ #
    # Legacy load (used by some callers expecting plain text)
    # ------------------------------------------------------------------ #

    def load(self, force_reload: bool = False) -> Optional[str]:
        """返回已回答条目的纯文本（供 build_context_prompt 使用）。"""
        return self.build_context_prompt() or None

    def save(self, content: str) -> None:
        """兼容旧 textarea 保存接口（无操作，JSON 模式下不再使用）。"""
        logger.warning("ContextManager.save(str) 已废弃，请使用 update_entry()")

    # ------------------------------------------------------------------ #
    # LLM Prompt
    # ------------------------------------------------------------------ #

    def build_context_prompt(self) -> str:
        """构建用于 LLM 的上下文 prompt（已回答条目提供答案，待回答条目列出题目避免重复提问）。"""
        all_entries = self.list_entries()
        answered   = [e for e in all_entries if e.is_answered]
        unanswered = [e for e in all_entries if not e.is_answered]

        if not answered and not unanswered:
            return ""

        lines = ["## 项目背景说明\n"]

        for e in answered:
            lines.append(f"### {e.topic}")
            lines.append(e.answer)
            lines.append("")

        if unanswered:
            lines.append("### 以下问题已记录、待人工解答（请勿重复提问）\n")
            for e in unanswered:
                lines.append(f"- {e.topic}：{e.question}")
            lines.append("")

        return "\n".join(lines)


def get_combined_context(config: Optional[Config] = None) -> str:
    """获取组合上下文信息（术语表 + 背景说明）。"""
    from meeting_agent.glossary import GlossaryManager

    parts = []

    glossary_mgr = GlossaryManager(config)
    glossary_text = glossary_mgr.build_glossary_prompt()
    if glossary_text:
        parts.append(glossary_text)

    context_mgr = ContextManager(config)
    context_text = context_mgr.build_context_prompt()
    if context_text:
        parts.append(context_text)

    return "\n\n---\n\n".join(parts) if parts else ""
