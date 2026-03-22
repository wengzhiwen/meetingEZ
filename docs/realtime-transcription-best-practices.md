# 纯浏览器实时语音转写工具最佳实践（OpenAI Realtime API）

## 适用范围

本文面向这样的产品形态：

- 运行在浏览器中
- 目标是“实时语音转写”，而不是语音助手对话
- 希望前端尽可能轻，后端尽可能薄
- 需要可上线、可维护、可扩展的实现方案

本文的推荐基于 OpenAI 官方文档中的 Realtime WebRTC、Realtime transcription、VAD、client secret 和 Agents SDK 指南：

- Realtime with WebRTC: <https://developers.openai.com/api/docs/guides/realtime-webrtc>
- Realtime transcription: <https://developers.openai.com/api/docs/guides/realtime-transcription>
- Voice activity detection (VAD): <https://developers.openai.com/api/docs/guides/realtime-vad>
- Create client secret: <https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create>
- Agents SDK: <https://developers.openai.com/api/docs/guides/agents-sdk>

---

## 结论

对于“纯浏览器实时语音转写工具”，推荐的最佳实践是：

**Browser + WebRTC + Realtime transcription-only session + 极小后端签发 client secret**

这套方案的核心原因如下：

1. OpenAI 对浏览器客户端优先推荐 WebRTC，而不是直接走浏览器 WebSocket。
2. Realtime API 原生支持 transcription-only mode，适合只做转写、不做回答的产品。
3. 浏览器前端不应暴露标准 API key，应由后端签发短期有效的 client secret。
4. Agents SDK 更适合“语音 agent / 工具调用 / 多 agent 编排”，不适合把一个纯转写工具复杂化。

---

## 推荐架构

### 1. 前端职责

浏览器前端负责：

- 申请麦克风权限
- 采集音频
- 使用 WebRTC 与 OpenAI Realtime 建立连接
- 接收增量转写事件和完成事件
- 管理转写 UI、状态机、错误恢复

### 2. 后端职责

后端尽量薄，只做：

- 使用标准 API key 调用 OpenAI 创建 `client_secret`
- 将短期有效的 client secret 返回给浏览器

不建议后端介入音频流中转，除非你有非常明确的合规、审计、录制、企业代理或自定义媒体处理要求。

### 3. 为什么不是“零后端”

严格意义上，不建议 100% 无后端。因为浏览器不能安全保存标准 API key。官方为客户端场景提供了短期有效的 client secret，适合 web/mobile 客户端使用。因此最合理的产品形态不是“完全无后端”，而是“业务上几乎纯前端，安全上保留一个极小签发服务”。

---

## 连接方式选择

### 结论：浏览器优先 WebRTC

OpenAI 的 Realtime WebRTC 指南对浏览器客户端给出的方向很明确：优先使用 **WebRTC**。它更适合实时媒体流，通常也比浏览器直连 WebSocket 更稳。

### 为什么不优先浏览器 WebSocket

浏览器 WebSocket 并不是不能用，但它更适合：

- 服务端到服务端
- 你已经有自己的音频流协议/网关
- 你明确要自己管理更底层的音频传输

对于一个以浏览器为核心的实时转写产品，WebRTC 的综合收益通常更高：

- 媒体链路更自然
- 低延迟表现更好
- 官方路径更明确
- 更接近浏览器实时音频产品的常规架构

---

## API 模式选择

### 结论：使用 transcription-only mode

Realtime API 提供 transcription-only mode，适用于：

- 实时字幕
- 听写
- 会议转录
- 语音输入框

这比 conversation 模式更合适，因为你的产品并不需要模型“回答”用户，而是只需要稳定地把音频转换为文本。

### 为什么不需要 Agents SDK

Agents SDK 的目标是帮助你构建 agent 系统，例如：

- 工具调用
- handoff
- 多 agent 协作
- trace 和 guardrails
- 语音 agent 工作流

但纯实时转写工具的核心问题是：

- 低延迟音频接入
- 稳定分段
- 增量 transcript 渲染
- 错误恢复
- UI/状态一致性

因此在这个场景里，Agents SDK 不是首选，反而会引入不必要的抽象层。

---

## 会话配置最佳实践

### 1. 使用 transcription session

Realtime transcription 指南说明，这类会话应使用 `type: "transcription"`。这可以避免把会话误配置成对话模式。

### 2. 输入音频格式优先使用 24kHz 单声道 PCM

官方文档支持 `audio/pcm`（24kHz、单声道）。对于浏览器麦克风实时流，这是一个非常合适的默认值：

- 兼容性好
- 质量稳定
- 易于与前端采集链路对齐

电话类系统或兼容旧语音通道时，才更常考虑 G.711 μ-law / A-law。

### 3. 模型选择

Realtime transcription 文档列出了支持的转写模型，包括：

- `gpt-4o-transcribe`
- `gpt-4o-mini-transcribe`
- `gpt-4o-transcribe-latest`
- `whisper-1`

对于实时、增量、前端用户体验，我的建议是：

- 默认：`gpt-4o-transcribe`
- 预算更敏感时：`gpt-4o-mini-transcribe`
- 不优先选择：`whisper-1`

原因是官方文档说明，`gpt-4o-transcribe` / `gpt-4o-mini-transcribe` 更适合真正的流式增量 transcript，而 `whisper-1` 在 Realtime 中不会提供同样理想的逐步增量体验。

### 4. 已知语言时显式设置 `language`

如果你的产品场景是单语种或可预知语种，建议显式传入 ISO-639-1 语言码。这样通常能：

- 降低误识别
- 减少语言自动判定抖动
- 提升整体稳定性

只有在明显的多语混说场景下，才建议依赖自动语言识别。

### 5. 使用 `prompt` 注入术语表

官方文档支持使用 `prompt` 提供引导文本或关键词。对于真实产品，这非常有价值。

建议把这些内容注入 `prompt`：

- 产品名
- 品牌名
- 人名
- 地名
- 行业术语
- 缩写词
- 容易识错的专有名词

这比事后做文本替换更稳，因为它直接影响模型识别过程。

---

## VAD 最佳实践

### 结论：先用 `server_vad`

Realtime VAD 指南给出了两种 turn detection 方式：

- `server_vad`
- `semantic_vad`

对于实时转写工具，建议先从 **`server_vad`** 起步。

原因：

- 行为更可预测
- 参数更容易调优
- 更适合字幕/听写这种“尽快出字”的场景

### 推荐起始参数

建议从下面的范围开始调：

- `threshold`: `0.45 ~ 0.60`
- `prefix_padding_ms`: `200 ~ 300`
- `silence_duration_ms`: `300 ~ 600`

可以把它们理解为：

- `threshold`：环境越嘈杂，通常越需要提高
- `prefix_padding_ms`：避免切掉句首
- `silence_duration_ms`：越小越快出结果，但也越容易切得过碎

### 什么时候考虑 `semantic_vad`

如果你的用户主要是长句口语、思考停顿多、说话节奏不规则，可以尝试 `semantic_vad`。它更关注“说完没有”而不只是“静音了没有”。

但在产品第一版中，我仍建议先落在 `server_vad`，因为更容易建立清晰、可解释、可复现的工程参数体系。

### 什么时候关闭 VAD

只有在以下场景，我才建议关闭自动 VAD 并改为手动提交：

- 你有按住说话/松开发送的交互
- 前端已经实现了自己的高质量分段策略
- 你希望完全掌控 turn commit 时机

否则，保留服务端 VAD 通常是更省心的方案。

---

## 噪声处理最佳实践

Realtime transcription 支持 `audio.input.noise_reduction`，且噪声处理发生在 VAD 和 turn detection 之前。

可选值包括：

- `near_field`
- `far_field`
- `null`

### 推荐策略

- 笔记本内置麦克风、耳机麦克风、近距离讲话：`near_field`
- 会议室、桌面远场拾音、外放环境：`far_field`
- 你自己已经有成熟前端降噪链路：可尝试 `null`

### 默认建议

第一版产品建议直接用默认的 `near_field`，除非你明确知道自己是远场拾音产品。

---

## 前端状态机最佳实践

这是整个产品最容易被低估、但最关键的一层。

### 需要关注的关键事件

Realtime transcription 文档中最关键的事件包括：

- `conversation.item.input_audio_transcription.delta`
- `conversation.item.input_audio_transcription.completed`

如果启用了 VAD，还会看到：

- `input_audio_buffer.speech_started`
- `input_audio_buffer.speech_stopped`

### 正确的 UI 模型：双层文本状态

建议把转写状态分成两层：

#### 1. Live transcript

用于显示当前正在识别的文本。

特点：

- 来自 `delta`
- 实时刷新
- 可以采用更轻的视觉样式
- 可标记为“识别中”

#### 2. Final transcript

用于显示已经完成确认的一段文本。

特点：

- 来自 `completed`
- 一旦落库/落列表后不轻易回滚
- 允许复制、导出、打时间戳

### 必须按 `item_id` 建模

官方文档明确提醒：不同 speech turn 的 completion 事件顺序并不保证严格按你主观期望到达。因此，**不要按事件到达顺序直接拼接全文**。

正确做法是：

- 以 `item_id` 为主键维护转写条目
- `delta` 更新对应 item 的 live buffer
- `completed` 将对应 item 状态切为 final
- 最终 transcript 列表按你自己的时间轴或提交顺序渲染

这是生产实现里非常关键的一条。

---

## 置信度与可疑文本标记

Realtime transcription 支持通过 `include: ["item.input_audio_transcription.logprobs"]` 返回 logprobs。官方文档说明，这可以用于估算 transcription confidence。

### 推荐做法

不建议在普通用户 UI 上直接显示“92% 置信度”这样的数字。

更好的用法是：

- 低置信 token 做轻微高亮或下划线
- 低置信整段做“建议复核”标记
- 在高级模式或质检后台里显示更详细的 confidence 指标

这样既能利用 logprobs 带来的价值，又不会让用户对置信度数字产生误解。

---

## 浏览器端音频工程建议

### 1. 保持采集链路简单

建议：

- 单声道
- 尽量少做重复重采样
- 不要前端叠加过多花哨 DSP
- 优先保证连续、稳定的音频流

原因是 Realtime 已经提供明确的输入格式与噪声处理能力。前端过度加工音频，有时会适得其反。

### 2. 不要频繁断线重连

client secret 用于安全创建会话，但会话建立后，不应为了“token 快到期”而主动频繁重建正在工作的连接。更重要的是保持一段转写任务期间的链路稳定。

### 3. 做好断线恢复

生产级产品至少要有：

- 自动重连
- UI 明确提示“转写已中断 / 已恢复”
- 当前段文本的恢复策略
- 已完成段与未完成段的边界策略

断线恢复不是某个单一 API 参数能替你解决的，它是前端状态机设计的一部分。

---

## 成本、延迟、准确率的取舍

### 如果你更看重低延迟

优先策略：

- WebRTC
- `server_vad`
- 较短的 `silence_duration_ms`
- 显式设置 `language`
- 使用小而准的术语 `prompt`

### 如果你更看重准确率

优先策略：

- `gpt-4o-transcribe`
- 正确配置 `noise_reduction`
- 维护术语表
- 使用 logprobs 做可疑文本复核

### 如果你更看重成本

优先策略：

- `gpt-4o-mini-transcribe`
- 通过 VAD 避免大量无意义静音
- 减少过碎的 turn
- 不要把无价值的长静音一直保留在活跃转写中

---

## 不建议做的事情

### 1. 不要把标准 API key 放在浏览器里

这不是可上线方案。正确做法是使用后端签发的短期 client secret。

### 2. 不要在第一版里引入 Agents SDK

你的核心挑战不是 agent orchestration，而是实时媒体与前端状态一致性。

### 3. 不要用“收到顺序”拼 transcript

必须按 `item_id` 关联与管理。

### 4. 不要在前端做过度音频魔改

先让链路稳定，再讨论花哨优化。

### 5. 不要同时把“实时字幕”“会议纪要总结”“语音助手回答”混成同一条主链路

第一版最好先把“实时转写”本身做对。摘要、翻译、关键词提取、说话人分离等能力应作为独立后处理或旁路能力加入。

---

## 推荐的默认配置

下面是一组适合作为 V1 起点的默认配置思路：

- 连接方式：WebRTC
- 会话类型：`transcription`
- 音频格式：24kHz mono PCM
- 模型：`gpt-4o-transcribe`
- 语言：已知则显式指定
- 术语提示：开启，维护一份产品词表
- 噪声处理：`near_field`
- VAD：`server_vad`
  - `threshold: 0.5`
  - `prefix_padding_ms: 300`
  - `silence_duration_ms: 400 ~ 500`
- 事件消费：`delta` + `completed`
- 数据建模：按 `item_id` 管理 live/final 状态
- 置信度：启用 logprobs，但默认不直接显示百分比

---

## 上线前检查清单

### 安全

- [ ] 浏览器不包含标准 API key
- [ ] 后端仅签发 client secret
- [ ] client secret 生命周期合理

### 实时链路

- [ ] 浏览器使用 WebRTC
- [ ] 麦克风权限被正确处理
- [ ] 断线后有明确恢复策略

### 转写质量

- [ ] 明确模型选择
- [ ] 已配置语言或明确采用自动识别
- [ ] 已维护术语 prompt
- [ ] 已选择合适的噪声处理模式

### 状态管理

- [ ] `delta` 与 `completed` 分层处理
- [ ] 以 `item_id` 作为主键
- [ ] UI 能区分 live/final

### 可用性

- [ ] 用户能感知“正在收音/正在识别/已完成/已中断”
- [ ] 可疑文本有轻量提示
- [ ] 导出/复制只面向 final transcript

---

## 最终建议

如果你的目标是尽快做出一个真正可上线的浏览器实时转写产品，我建议你坚持下面这条主线：

**用 WebRTC 接 Realtime transcription，用极小后端签 client secret，用 `server_vad` 做第一版分段，用 `item_id` 驱动前端状态机，用术语表与 logprobs 做质量增强。**

这条路线的优点是：

- 架构简单
- 安全边界清晰
- 与官方推荐路径一致
- 产品体验容易调优
- 后续也容易扩展到摘要、翻译、关键词、会议纪要等能力

如果以后你从“转写工具”升级成“会听、会查、会答的语音助手”，那时再考虑引入 Agents SDK，会更合适。
