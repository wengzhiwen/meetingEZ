2026-08-08 已知成员不再进入术语/背景建议
- **人员角色注入分析 prompt**：`_project.json` 团队名单（姓名/昵称/职位）通过 `people_info` 参数传入分析 prompt（该参数此前一直闲置），prompt 明确禁止对已知成员再提术语建议或待解释问题
- **确定性后置过滤**：新增 `ProjectConfig.matches_known_person()`，姓名/昵称双向包含匹配、职位精确匹配；术语建议按 `canonical`、待解释问题按 `topic` 在写入前过滤，过滤数量在 CLI 输出中提示

2026-08-08 移除行动项子系统
- **纪要不再整理行动项**：分析 prompt 移除 `new_actions` / `completed_actions` / `mentioned_actions` 输出字段和"现有待办"输入段，各会议纪要模板删除行动项表格
- **删除待办追踪子系统**：移除 `ActionsManager`（`memory/actions.py`）、`actions` / `complete` CLI 命令、`status` 待办统计、context.md 待办段落、会前提示中的待办相关项
- **Web 同步移除**：项目卡片 `actions_total` / `actions_overdue` 统计、概览页"逾期" pill、Context Pack 的 `pendingActions`，实时页上下文摘要不再包含近期行动项
- 既有项目目录里的 `actions.md` 数据文件保留在磁盘上，仅不再生成和读取
- **prompt 增加显式禁令**：移除字段后 GPT-5.6 仍会模仿 context.md 旧"待办"段落和纪要写作惯例自行生成"行动项"章节，系统提示和输出要求中补充明确禁止，责任人/截止时间信息并入议题讨论记录或关键决策

2026-08-08 纪要模型升级 GPT-5.6 与调用方式迁移
- **纪要生成模型升级**：`gpt-5.4` → `gpt-5.6-sol`，默认 `reasoning.effort = high`（新增 `OPENAI_REASONING_EFFORT` 环境变量）
- **纪要调用迁移到 Responses API**：`LLMClient` 从 Chat Completions 改为 `client.responses.create`（`instructions` + `input` + `reasoning`）；GPT-5.6 只支持默认 temperature，不再下发该参数
- **后置处理默认 effort 统一为 high**：`TRANSLATION_REASONING_EFFORT`、`REFINE_REASONING_EFFORT` 默认从 `low` 改为 `high`；实时字幕后置翻译/术语校正会更准但更慢，延迟敏感可用环境变量降回 `low`
- **翻译兜底模型清理**：`TRANSLATION_MODEL` 代码兜底默认从 `gpt-5.4-mini-2026-03-17` 改为 `gpt-5.6-luna`，与 `.env` 生产固定值一致
- **基础设施修复**：nginx 不再拦截会议文件接口的 `.log` URL，`_processing.log` 可在线预览

2026-03-23 控制台入口与项目协同流程重构
- **默认入口改为控制台**：`/` 不再直接进入实时字幕页，而是先进入控制台
- **新增双入口流程**：控制台同时提供 `项目模式` 和 `快速模式`
- **新增实时页独立路由**：实时转写页面改为 `/realtime`
- **新增 Web 建会流程**：控制台可直接创建会议目录和 `_meeting.json`，再跳转到实时页
- **新增项目增强包快速分流**：项目模式加载 context pack，快速模式返回空增强包但保留实时转写能力
- **实时页状态更明确**：顶部显示当前是项目模式还是快速模式，并展示当前会议上下文
- **文档同步更新**：README、USAGE、API 文档改为控制台优先的使用流程

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
