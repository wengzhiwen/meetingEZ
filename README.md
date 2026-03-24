# MeetingEZ

轻量的浏览器实时会议转写与按需翻译工具。

当前实现基于：
- 浏览器采集麦克风或标签页音频
- OpenAI Realtime API WebRTC transcription session
- 极小 Flask 后端签发 `client secret`
- 后端代理翻译请求，前端不再保存 API Key

## 当前特性

- 控制台首页：默认先进入控制台，再选择项目模式或快速模式
- 实时转写：`gpt-4o-transcribe`
- 连接方式：WebRTC + DataChannel
- 分段策略：`semantic_vad`，`eagerness: "high"`
- 后置翻译：默认 `gpt-5.4-mini-2026-03-17`，可用 `TRANSLATION_MODEL` 覆盖
- 推理强度：默认 `low`，可用 `TRANSLATION_REASONING_EFFORT` 覆盖，仅对支持 reasoning 的翻译模型生效
- 访问控制：可选 `ACCESS_CODE` 登录页
- 项目协同：项目模式下可加载术语、近期会议和待办摘要增强实时处理
- UI：控制台首页 + 实时页全屏字幕视图 + 底部吸附工具栏 + 右下角设置浮层
- 本地能力：自动滚动、下载 TXT、清空记录、字体大小、本地存储

## 快速开始

```bash
source venv/bin/activate
pip install -r requirements.txt
python run.py
```

默认地址：`http://localhost:5090`

## 环境变量

核心配置见 [`env.example`](/home/wengzhiwen/meetingEZ/env.example)：

- `OPENAI_API_KEY`
  用于 Realtime session 和翻译代理。
- `ACCESS_CODE`
  可选。为空时不启用登录保护。
- `TRANSLATION_MODEL`
  可选。默认 `gpt-5.4-mini-2026-03-17`。
- `TRANSLATION_REASONING_EFFORT`
  可选。默认 `low`；仅对支持 reasoning 的翻译模型生效。
- `SECRET_KEY`
  Flask session 密钥。

## 使用方式

1. 打开页面。
2. 如配置了 `ACCESS_CODE`，先登录。
3. 默认进入控制台，有两条入口：
   - `快速转写`：不关联项目，直接进入实时页
   - `项目模式`：先选项目、建立会议，再进入实时页
4. 进入实时页后，在设置浮层中选择或确认：
   - 音频输入源
   - 麦克风设备
   - 主要语言 / 第二语言
   - 语言模式
   - 字体大小
5. 点击底部工具栏中的 `开始`。
6. 会议进行中：
   - 底部显示音量条和计时
   - 字幕实时滚动
   - 顶部显示当前是项目模式还是快速模式
   - 可下载或清空记录
7. 点击同一个按钮结束会议。

## 文档索引

- 实现接口：[`docs/API.md`](/home/wengzhiwen/meetingEZ/docs/API.md)
- 当前音频/前端架构：[`docs/AUDIO_ARCHITECTURE.md`](/home/wengzhiwen/meetingEZ/docs/AUDIO_ARCHITECTURE.md)
- 使用说明：[`docs/USAGE.md`](/home/wengzhiwen/meetingEZ/docs/USAGE.md)
- 变更记录：[`docs/CHANGELOG.md`](/home/wengzhiwen/meetingEZ/docs/CHANGELOG.md)
- 术语约定：[`docs/TERMINOLOGY.md`](/home/wengzhiwen/meetingEZ/docs/TERMINOLOGY.md)
- Realtime 实践笔记：[`docs/realtime-transcription-best-practices.md`](/home/wengzhiwen/meetingEZ/docs/realtime-transcription-best-practices.md)
- 会议纪要 Agent：[`docs/meeting_minutes_agent.md`](/home/wengzhiwen/meetingEZ/docs/meeting_minutes_agent.md)

## 会议纪要 Agent

仓库仍包含离线会议录音处理的命令行 Agent，用于：
- 录音转写
- 会议纪要生成
- 项目记忆维护

详见 [`docs/meeting_minutes_agent.md`](/home/wengzhiwen/meetingEZ/docs/meeting_minutes_agent.md)。

## 许可证

MIT
