"""
LLM 客户端 - OpenAI GPT-5.4
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from meeting_agent.config import Config
from meeting_agent.llm.prompts import PromptBuilder
from meeting_agent.models import (
    GPTAnalysisResult,
    MeetingMeta,
    MeetingType,
)

logger = logging.getLogger("meeting_agent.llm")


class LLMClient:
    """OpenAI GPT 客户端"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.api_key = self.config.openai_api_key
        self.base_url = self.config.settings.openai_base_url
        self.model = self.config.settings.openai_model
        self._client = None

    @property
    def client(self):
        """延迟初始化 OpenAI 客户端"""
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    def analyze_meeting(
        self,
        transcript_text: str,
        meeting_meta: Optional[MeetingMeta] = None,
        project_context: Optional[str] = None,
        existing_actions: Optional[str] = None,
        recent_minutes: Optional[list[str]] = None,
        pre_hint: Optional[str] = None,
        people_info: Optional[str] = None,
        glossary_context: Optional[str] = None,
        has_speaker_info: bool = False,
    ) -> Optional[GPTAnalysisResult]:
        """
        分析会议内容，生成纪要和更新

        Args:
            transcript_text: 转写文本
            meeting_meta: 会议元信息
            project_context: 项目上下文 (context.md 内容)
            existing_actions: 现有待办 (actions.md 内容)
            recent_minutes: 最近的会议纪要列表
            pre_hint: 会议前提示
            people_info: 人员信息
            glossary_context: 术语表和人工维护的上下文 (_context.md)
            has_speaker_info: 转写文本是否包含说话人信息

        Returns:
            GPTAnalysisResult 或 None
        """
        # 构建 Prompt
        prompt = PromptBuilder.build_analysis_prompt(
            transcript_text=transcript_text,
            meeting_meta=meeting_meta,
            project_context=project_context,
            existing_actions=existing_actions,
            recent_minutes=recent_minutes,
            pre_hint=pre_hint,
            people_info=people_info,
            glossary_context=glossary_context,
            has_speaker_info=has_speaker_info,
        )

        # 调用 GPT
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PromptBuilder.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_completion_tokens=16000,  # GPT-5.4 使用 max_completion_tokens
            )

            content = response.choices[0].message.content
            if not content:
                logger.error("GPT 返回空内容")
                return None

            # 解析 JSON
            result = self._parse_json_response(content)
            if result:
                return GPTAnalysisResult(**result)

        except Exception as e:
            logger.error("调用 GPT 失败: %s", e)

        return None

    def generate_pre_meeting_hint(
        self,
        meeting_meta: MeetingMeta,
        context_md: Optional[str] = None,
        actions_md: Optional[str] = None,
    ) -> Optional[str]:
        """
        生成会议前提示

        Args:
            meeting_meta: 会议元信息
            context_md: 项目上下文
            actions_md: 现有待办

        Returns:
            会议前提示 Markdown 或 None
        """
        prompt = PromptBuilder.build_pre_meeting_hint_prompt(
            meeting_meta=meeting_meta,
            context_md=context_md,
            actions_md=actions_md,
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一位专业的项目管理助手，负责在会议前生成提示清单，帮助参会者做好准备。"
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_completion_tokens=4096,  # GPT-5.4 使用 max_completion_tokens
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error("生成会议前提示失败: %s", e)
            return None

    def _parse_json_response(self, content: str) -> Optional[dict[str, Any]]:
        """解析 GPT 返回的 JSON"""
        # 尝试直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 块
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试找到 JSON 对象
        brace_start = content.find("{")
        brace_end = content.rfind("}")
        if brace_start != -1 and brace_end != -1:
            try:
                return json.loads(content[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass

        logger.error("无法解析 JSON 响应")
        return None

    def is_available(self) -> bool:
        """检查 LLM 是否可用"""
        return bool(self.api_key)
