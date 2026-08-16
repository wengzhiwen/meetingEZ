# MeetingEZ

轻量的浏览器实时会议转写与双语流式翻译工具。

- 音频来自浏览器采集：麦克风、Chrome 标签页，或 macOS 采集器（其他应用 / 整个系统的音频，见 [`native-capture/`](native-capture/README.md)）
- OpenAI Realtime WebRTC session 实时转写与翻译；三栏字幕（原文 + 两种目标语言，原文栏可隐藏）
- 极小 Flask 后端只签发短期 `client secret`，API Key 不进浏览器
- 云端模型全部来自 OpenAI（实时翻译 `gpt-realtime-translate`、术语校正 `gpt-5.6-luna`、离线转写 `gpt-transcribe`、纪要 `gpt-5.6-sol`）；也可切换本地 Qwen3-ASR 引擎（见[本地模型](#本地模型qwen3-asr)）

## 当前特性

- 控制台首页 + 实时页全屏字幕：快速转写 / 项目模式两条入口
- 实时会话：2 路 translation session 分别译入两种目标语言，第 0 路 input transcript 兼任权威原文
- 术语校正：realtime 层注入不了术语表，由定格后的文本链路按项目术语表纠错、原位替换
- 音频输入：麦克风 / 标签页音频 / macOS 应用与系统音频（可叠加本地麦克风混音）
- 访问控制（可选 `ACCESS_CODE`）、项目协同、自动滚动、导出 TXT、本地存储

## 快速开始

```bash
source venv/bin/activate
pip install -r requirements.txt
python run.py
```

默认地址 `http://localhost:5090`，核心配置见 [`env.example`](env.example)。

## 环境变量

- `OPENAI_API_KEY` — 全系统唯一模型凭据（Realtime / 翻译 / 离线 ASR / 纪要）
- `ACCESS_CODE` — 可选，为空不启用登录
- `REALTIME_TRANSLATION_MODEL` — 实时翻译模型，默认 `gpt-realtime-translate`
- `REALTIME_TRANSLATION_INPUT_MODEL` — 翻译 session 的输入转写模型，默认 `gpt-live-transcribe`
- `REFINE_MODEL` — 术语校正模型，默认 `gpt-5.6-luna`
- `OPENAI_ASR_MODEL` — 离线文件转写模型，默认 `gpt-transcribe`
- `TRANSCRIPTION_MODEL` — 独立转写 session 模型（当前默认链路不使用，见 `docs/API.md`）
- `LOCAL_ASR_BASE_URL` — 本地 Qwen3-ASR 端点默认值（前端可改）
- `SECRET_KEY` — Flask session 密钥

## 使用方式

1. 打开页面（配置了 `ACCESS_CODE` 先登录）；
2. 控制台选择快速转写或项目模式进入实时页；
3. 设置浮层中确认音频输入源、目标语言等；
4. 点 `开始`，字幕实时滚动；再点一次结束。

## 本地模型（Qwen3-ASR）

实时页的转写引擎可切换为本地部署的 Qwen3-ASR 流式服务，音频不经过云端：

- 部署仓库：[wengzhiwen/Qwen3-ASR](https://github.com/wengzhiwen/Qwen3-ASR)
  （fork 自 [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)，针对 GB10 / DGX Spark
  做了流式服务强化：vLLM 兼容、VAD 静音分段与停滞检测、`deploy/` Docker 一键部署）
- 使用方式：启动流式服务后，实时页设置中填入端点并选择「本地 Qwen3-ASR」引擎；可配合
  本地 OpenAI 兼容 API 做翻译校验

## 文档索引

- 实现接口：[docs/API.md](docs/API.md)
- 音频/前端架构：[docs/AUDIO_ARCHITECTURE.md](docs/AUDIO_ARCHITECTURE.md)
- 使用说明：[docs/USAGE.md](docs/USAGE.md)
- 变更记录：[docs/CHANGELOG.md](docs/CHANGELOG.md)
- 术语约定：[docs/TERMINOLOGY.md](docs/TERMINOLOGY.md)
- Realtime 实践笔记：[docs/realtime-transcription-best-practices.md](docs/realtime-transcription-best-practices.md)
- 会议纪要 Agent：[docs/meeting_minutes_agent.md](docs/meeting_minutes_agent.md)
- macOS 本地采集器：[native-capture/README.md](native-capture/README.md)

## 会议纪要 Agent

仓库含离线会议录音处理的命令行 Agent（转写、纪要生成、项目记忆维护），
详见 [docs/meeting_minutes_agent.md](docs/meeting_minutes_agent.md)。

## 许可证

MIT
