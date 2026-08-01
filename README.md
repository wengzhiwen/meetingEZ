# MeetingEZ

轻量的浏览器实时会议转写与双语流式翻译工具。

当前实现基于：
- 浏览器采集麦克风或标签页音频
- OpenAI Realtime transcription + Realtime Translation WebRTC session
- 极小 Flask 后端签发 `client secret`
- 前端不保存 OpenAI API Key
- 三栏显示原文与两种目标语言译文，原文栏可隐藏

模型全部来自 OpenAI：实时翻译 `gpt-realtime-translate`、术语校正 `gpt-5.6-luna`、
离线文件转写 `gpt-transcribe`、纪要生成 `gpt-5.4`。

## 当前特性

- 控制台首页：默认先进入控制台，再选择项目模式或快速模式
- 连接方式：WebRTC + DataChannel
- 实时会话结构：2 路 `gpt-realtime-translate` session，分别译入第一语言和第二语言；第 0 路的 input transcript 同时作为权威原文
- 术语校正：realtime session 注入不了术语表，改由定格后的文本链路补——字幕先直接上屏，随后交给 `gpt-5.6-luna` 按项目术语表纠错，返回后原位替换
- Translation transcript delta 不依赖 `item_id`，按当前流累积，并以 done 事件或短暂停顿完成分段
- 访问控制：可选 `ACCESS_CODE` 登录页
- 项目协同：项目模式下关联会议目录和项目上下文
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
  全系统唯一的模型凭据：Realtime session、翻译代理、离线 ASR、纪要生成。
- `ACCESS_CODE`
  可选。为空时不启用登录保护。
- `REFINE_MODEL`
  字幕术语校正模型，默认 `gpt-5.6-luna`。
- `TRANSCRIPTION_MODEL`
  独立实时转写 session 的模型，默认 `gpt-live-transcribe`（当前默认链路不使用，见 `docs/API.md`）。
- `REALTIME_TRANSLATION_MODEL`
  实时翻译模型，默认 `gpt-realtime-translate`。
- `REALTIME_TRANSLATION_INPUT_MODEL`
  Realtime Translation 内部的输入转写模型，默认 `gpt-live-transcribe`。
- `OPENAI_ASR_MODEL`
  离线文件转写模型，默认 `gpt-transcribe`。
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
   - 两种不同的目标语言
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
