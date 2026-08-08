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

OpenAI 在 2026-07 发布了两个新转写模型，本项目已全量迁移过去：

- `gpt-live-transcribe`：低延迟流式转写，只支持 `v1/realtime/transcription_sessions`。
  官方 Real World Audio Benchmark 上 WER 从 11.65%（`gpt-realtime-whisper`）降到 9.60%。
  价格同为 $0.017/分钟。
- `gpt-transcribe`：完成文件/批量转写，只支持 `v1/audio/transcriptions`。
  WER 从 15.21% 降到 8.98%，$0.0045/分钟。

对于实时、增量、前端用户体验的建议：

- 实时字幕：`gpt-live-transcribe`
- 离线文件转写：`gpt-transcribe`
- 上一代 `gpt-realtime-whisper` / `gpt-4o-transcribe` / `whisper-1` 不再推荐：
  同价或更贵、WER 更差，且不支持 `keywords`。

### 4. 用 `languages` 给语言提示，而不是 `language` 硬锁

`gpt-live-transcribe` 接受 `languages` 数组（ISO-639-1 短码），是**提示不是限制**。
双语会议把两种语言都给上，中英/中日码切换时比单个 `language` 更稳，也比完全自动检测
少抖动。上一代模型只有单数的 `language`，那是硬指定。

### 5. 术语表走 `keywords`，不要走 `prompt`

`gpt-live-transcribe` 和 `gpt-transcribe` 都接受两类上下文：

- `prompt`：自由文本的**录音场景描述**（主题、场合、背景），不是指令。
- `keywords`：**希望模型写对的字面词**数组——人名、产品名、缩写、药品名。

实测有效（gpt-transcribe，同一段 TTS 音频）：

| | 输出 |
|---|---|
| 不带 keywords | 我们这次在 **Meeting EZ** 里接入了 Chirp 3 和 **Vibe Voice** |
| 带 keywords | 我们这次在 **MeetingEZ** 里接入了 Chirp 3 和 **VibeVoice** |

两个注意点：

- **只喂 canonical，不要喂别名。** 别名是既有的识别错误，作为 keywords 会强化错误拼写。
- **Realtime Translation session 不接受这些字段。** `audio.input.transcription` 下只允许
  `model`，其余会 400 `unknown_parameter`。

如果你的链路是 translation session（拿不到 `keywords`），还有第二条路：**后置文本校正**。
本项目走的就是这条——realtime 字幕先上屏，定格后批量交给便宜的文本模型按术语表纠错，
返回再原位替换。本项目默认 `effort=high`（质量优先）；若压字幕延迟可降到 `low`，
实测 `gpt-5.6-luna` + `effort=low` 约 4 秒 / 6 句，$0.20/$1.20 每百万 token，
比多开一路 realtime session（$0.017/分钟）便宜一个数量级。代价是术语正确的文本会晚几秒出现。

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

- 实时字幕链路使用 `gpt-live-transcribe`，并用真实会议音频评估延迟与准确率
- 如果不是实时流式场景，用 `gpt-transcribe`
- 维护 `keywords`：人名和专有名词的收益最大
- 正确配置 `noise_reduction`
- 维护术语表
- 使用 logprobs 做可疑文本复核

### 如果你更看重成本

优先策略：

- 离线批量走 `gpt-transcribe`（$0.0045/分钟，比实时便宜近 4 倍）
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
- 模型：`gpt-live-transcribe`
- 延迟：`delay` 档位可配（`TRANSCRIPTION_DELAY`，默认 `low`，可选 minimal/low/medium/high/xhigh）
- 语言：`languages` 数组给提示，双语会议把两种都给上
- 术语提示：`keywords` 传项目术语表 canonical + 人员标准名；`prompt` 传项目背景
- 噪声处理：`near_field`
- VAD：不发送 `turn_detection`
- 分段/出字：依赖 `delay`（默认 `low`）的低延迟流式 transcript deltas
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
- [ ] 重要术语已纳入人工复核清单
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
