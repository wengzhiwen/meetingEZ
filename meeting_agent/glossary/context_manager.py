"""
项目上下文管理模块

_context.md 文件用于存储人工维护的解释性内容，
帮助 LLM 更好理解会议中的专业术语、业务概念等。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from meeting_agent.config import Config

logger = logging.getLogger("meeting_agent.context")


CONTEXT_TEMPLATE = """# 项目上下文

> 此文件用于存储项目的关键信息，帮助 AI 更好地理解会议内容。
> 请根据实际情况补充和更新以下内容。

## 项目简介

<!-- 描述项目的核心目标和定位 -->


## 核心概念

### 概念1
<!-- 解释这个概念是什么，为什么重要 -->


### 概念2
<!-- 解释这个概念是什么，为什么重要 -->


## 技术架构

<!-- 简要说明技术栈、架构特点 -->


## 团队角色

<!-- 说明团队成员及其职责 -->


## 业务流程

<!-- 说明核心业务流程 -->


## 常见问题

<!-- 记录常见的问题和解决方案 -->


## 备注

<!-- 其他需要说明的内容 -->
"""


class ContextManager:
    """项目上下文管理器"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self._context_cache: Optional[str] = None

    @property
    def context_file(self) -> Path:
        return self.config.meetings_dir / "_context.md"

    def load(self, force_reload: bool = False) -> Optional[str]:
        """
        加载项目上下文

        Args:
            force_reload: 是否强制重新加载

        Returns:
            上下文内容或 None
        """
        if self._context_cache is not None and not force_reload:
            return self._context_cache

        if not self.context_file.exists():
            logger.debug("上下文文件不存在: %s", self.context_file)
            return None

        try:
            self._context_cache = self.context_file.read_text(encoding="utf-8")
            logger.info("加载项目上下文: %s", self.context_file)
            return self._context_cache
        except Exception as e:
            logger.warning("加载上下文失败: %s", e)
            return None

    def save(self, content: str) -> None:
        """保存项目上下文"""
        self.context_file.write_text(content, encoding="utf-8")
        self._context_cache = content
        logger.info("保存项目上下文: %s", self.context_file)

    def initialize(self) -> None:
        """初始化项目上下文文件"""
        if self.context_file.exists():
            logger.info("上下文文件已存在，跳过初始化")
            return

        self.save(CONTEXT_TEMPLATE)
        logger.info("初始化项目上下文: %s", self.context_file)

    def append_questions(
        self,
        questions: list[dict[str, str]],
        source_meeting: Optional[str] = None,
    ) -> int:
        """
        追加需要人工解释的问题到上下文文件

        Args:
            questions: 问题列表，每个包含 topic, question, reason
            source_meeting: 来源会议目录名

        Returns:
            追加的问题数量
        """
        if not questions:
            return 0

        existing = self.load() or ""

        # 过滤已存在的问题（检查标题是否已存在）
        new_questions = []
        for q in questions:
            topic = q.get("topic", "未知主题")
            # 检查是否已存在该主题（作为 ### 标题）
            if f"### {topic}" in existing:
                logger.debug("问题已存在，跳过: %s", topic)
                continue
            new_questions.append(q)

        if not new_questions:
            return 0

        # 构建待解释部分
        lines = ["\n\n---\n\n## 待补充的解释\n"]
        if source_meeting:
            lines.append(f"> 以下问题来自会议: {source_meeting}\n")

        for q in new_questions:
            topic = q.get("topic", "未知主题")
            question = q.get("question", "")
            reason = q.get("reason", "")

            lines.append(f"### {topic}\n")
            lines.append(f"<!-- 问题: {question} -->\n")
            lines.append(f"<!-- 原因: {reason} -->\n")
            lines.append("<!-- 请在此处补充解释 -->\n\n")

        # 追加到文件
        new_content = existing + "".join(lines)
        self.save(new_content)

        logger.info("追加 %d 个待解释问题到: %s", len(questions), self.context_file)
        return len(questions)

    def build_context_prompt(self) -> str:
        """
        构建用于 LLM 的上下文 prompt

        Returns:
            用于 prompt 的上下文文本
        """
        content = self.load()

        if not content:
            return ""

        # 过滤掉空行和注释，提取有效内容
        lines = content.split("\n")
        effective_lines = []

        for line in lines:
            # 跳过空行
            if not line.strip():
                continue
            # 跳过 HTML 注释
            if "<!--" in line and "-->" in line:
                continue
            # 跳过只有注释的行
            stripped = line.strip()
            if stripped.startswith("<!--") and stripped.endswith("-->"):
                continue
            effective_lines.append(line)

        if not effective_lines:
            return ""

        return f"""## 项目上下文（由用户维护）

{content}

*请参考以上项目上下文来理解会议内容。*
"""


def get_combined_context(config: Optional[Config] = None) -> str:
    """
    获取组合的上下文信息（术语表 + 项目上下文）

    Args:
        config: 配置对象

    Returns:
        组合的上下文 prompt
    """
    from meeting_agent.glossary import GlossaryManager

    parts = []

    # 术语表
    glossary_mgr = GlossaryManager(config)
    glossary_text = glossary_mgr.build_glossary_prompt()
    if glossary_text:
        parts.append(glossary_text)

    # 项目上下文
    context_mgr = ContextManager(config)
    context_text = context_mgr.build_context_prompt()
    if context_text:
        parts.append(context_text)

    return "\n\n---\n\n".join(parts) if parts else ""
