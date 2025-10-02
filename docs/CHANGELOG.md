2025-10-02 重大架构升级：零音频丢失架构
- **AudioWorklet + Web Worker 双线程架构**：彻底解决音频丢失问题
- 音频采集运行在独立的 AudioWorklet 线程，不受主线程任何操作影响
- WAV 编码移至 Web Worker 线程，主线程不再执行同步编码操作
- 支持编码和上传并发，多个任务可同时进行，降低端到端延迟
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
