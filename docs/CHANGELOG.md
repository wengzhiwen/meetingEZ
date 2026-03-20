2026-03-20 当前实现整理与界面重构
- **翻译默认模型升级**：默认翻译模型改为 `gpt-5.4-mini-2026-03-17`，默认 `reasoning.effort = low`
- **翻译增加可选 reasoning 配置**：新增 `TRANSLATION_REASONING_EFFORT`，仅在支持的翻译模型上发送 `reasoning.effort`
- **Realtime 接入统一为 WebRTC transcription-only**：后端通过 `/v1/realtime/client_secrets` 签发 client secret，前端通过 `/v1/realtime/calls` 完成 SDP 交换
- **VAD 策略改为 `semantic_vad`**：`eagerness: high`，改善长句连续讲话时长时间不出字的问题
- **翻译链路迁移到后端代理**：新增 `/api/translate`，前端不再直接调用 OpenAI Responses
- **翻译结果增加后端收口**：避免“第一语言翻译还是第一语言”这类同语种误翻
- **新增性能日志**：前端增加 `Realtime [perf]` / `UI [perf]`，后端增加统一 `[perf]` 日志
- **新增访问码登录**：支持 `ACCESS_CODE` 保护页面和 API，未配置时自动关闭登录保护
- **前端布局重构**：主页面改为全屏字幕区 + 底部吸附工具栏 + 设置浮层
- **交互收敛**：底部只保留一个开始/结束切换按钮，连接状态移入设置浮层，状态文本并入计时区域

2026-03-19 修复并重构实时流式转写模式
- **修复后端 session 创建**：endpoint 从 `/v1/realtime/transcription_sessions` 改为 GA endpoint `/v1/realtime/client_secrets`，model 改为 `gpt-realtime-1.5`，移除 beta header
- **修复 client_secret 提取**：`client_secret` 是 `{value, expires_at}` 对象，现正确提取 `.value` 字段
- **修复前端 WebSocket 连接**：URL 参数从 `session_id` 改为 `model`，使用 subprotocol 传递 ephemeral key 认证，移除 beta-era `openai-beta.realtime-v1` subprotocol
- **清理废弃代码**：移除不再需要的 `_configureSession()` 方法（session 已在后端创建时配置）
- **简化 `updateLanguage()`**：语言由 API 自动检测，无需运行时更新 session
- **更新文档**：API.md 新增实时流式模式章节，AUDIO_ARCHITECTURE.md 新增实时流式数据流描述

2025-10-02 重大架构升级：零音频丢失架构
- **AudioWorklet + Web Worker 双线程架构**：彻底解决音频丢失问题
- 音频采集运行在独立的 AudioWorklet 线程，不受主线程任何操作影响
- WAV 编码移至 Web Worker 线程，主线程不再执行同步编码操作
- 支持编码和上传并发，多个任务可同时进行，降低端到端延迟
- **结束会议时自动处理剩余音频**：确保最后不足 8 秒的音频段也被转写
- 在途转写和翻译任务在会议结束后继续完成，无内容丢失
- 新增 `audio-processor.js`（AudioWorklet 处理器）
- 新增 `wav-encoder-worker.js`（WAV 编码 Worker）
- 新增详细架构文档 `AUDIO_ARCHITECTURE.md`
- 移除已废弃的 ScriptProcessorNode，使用现代 AudioWorklet API

2025-10-02 新增：
- 转写后置处理生成结构化 JSON（浏览器端调用 `/v1/responses`）
- 异步回填流程：原文先显示，随后按需插入中文翻译
- 结束会议时先停止录音，但保留在途转写与翻译继续处理，避免丢失尾段
- 音频输入源选择：支持标准麦克风输入和浏览器标签页音频捕获（通过 `getDisplayMedia` API）
- 标签页音频捕获：适合转录运行在其他标签页中的远程会议（如 Google Meet、Zoom 网页版等）
- 标签页音频模式下显示操作提示，引导用户正确选择标签页和音频共享选项

2025-10-02 修订：
- 后置处理模型改为 `gpt-4.1-mini-2025-04-14`，并使用 `json_schema` 严格输出
- 主流程采样率调整为 48kHz（AudioWorklet 缓冲 2048）；麦克风测试为 24kHz
- 展示策略统一为"保留原文 + 需要时插入翻译行"，不覆盖原文
