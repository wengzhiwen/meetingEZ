# MeetingEZ Capture（macOS 本地音频采集器）

菜单栏常驻小程序：采集**其他应用**（Zoom/Teams 等会议客户端）或**整个系统**的音频输出，
通过本机 WebSocket 提供给 meetingEZ 实时页（`/realtime`）做实时转写/翻译。

```
┌─────────────┐  ScreenCaptureKit   ┌────────────────────┐   WebSocket(127.0.0.1:17642)
│ Zoom / 系统 │ ──────纯音频──────▶ │ MeetingEZ Capture  │ ──┬─ 文本帧: JSON 控制协议
└─────────────┘                    │ （本目录的程序）     │   └─ 二进制帧: 16kHz Float32 PCM
                                   └────────────────────┘            │
                                                                      ▼
                                                 meetingEZ 实时页（浏览器）
                                                 AudioWorklet → MediaStream → 两路
                                                 Realtime Translation session
```

设计原则：**采集器只解决采集**。一切选择 UI（应用列表、模式、麦克风混音）都在 web 端实现，
本程序没有配置界面，后续 web 端改版无需更新它。

## 要求

- macOS 14+（按应用过滤音频需要 Sonoma；开发与验证在 macOS 26.5 上进行）
- Swift 工具链：Xcode 或仅 Command Line Tools 均可（无第三方依赖）

## 构建与安装

```bash
cd native-capture

# 方式一：CLI 直接跑（开发调试）
swift build -c release
.build/release/meetingez-capture

# 方式二：组装菜单栏 .app（推荐日常使用）
./bundle-app.sh
mv "build/MeetingEZ Capture.app" ~/Applications/   # 固定路径，避免重复授权
open ~/Applications/"MeetingEZ\ Capture.app"
```

菜单栏图标（波形）：显示采集状态，菜单里可退出；端口被占用会弹窗报错。

## 权限（屏幕录制）

macOS 把"采集其他应用音频"归入**屏幕录制**权限（纯音频，不触碰画面）：

1. 首次使用：web 端选中「应用 / 系统音频」音源后点「申请权限」，或直接开始，
   采集器会触发系统授权弹窗；也可手动在 系统设置 → 隐私与安全性 → 屏幕录制 里勾选。
2. 授权后个别 macOS 版本需要**重启采集器进程**才生效——web 端会提示，
   菜单栏也有「重启采集器（使权限生效）」菜单项。
3. **TCC 授权与"路径 + 签名身份"绑定**：
   - ad-hoc 签名（默认）：每次 `bundle-app.sh` 重建后身份变化，需重新授权；
   - 建议一次性创建自签名代码签名证书并 `MEETINGEZ_SIGN_IDENTITY="MeetingEZ" ./bundle-app.sh`
     （见脚本头部注释），重建后无需重复授权；
   - `.app` 移动到固定路径（如 `~/Applications`）后再首次授权。
4. 正式分发给他人需要 Developer ID 签名 + 公证，超出本仓库范围。

## 运行时选项

```
meetingez-capture [--port N] [--allow-origin 值]... [--no-gui] [--mock-audio]
```

| 选项 | 说明 |
|---|---|
| `--port` | WebSocket 端口，默认 17642（web 端写死同值） |
| `--allow-origin` | 追加允许的页面 Origin（精确匹配，可多次）；`*` 全放行（仅联调） |
| `--no-gui` | 不启动菜单栏，纯服务模式（自动化/CI 用） |
| `--mock-audio` | 440Hz 假音源 + 固定应用列表，权限恒为已授予（web 端无权限联调用） |

## 与 web 端配合

1. 启动采集器（菜单栏出现波形图标）。
2. 打开 meetingEZ 实时页，音源选「应用 / 系统音频（macOS 采集器）」。
3. 状态行显示「已连接」后勾选要采集的应用（或勾「采集整个系统音频」）。
4. 需要转写自己的声音时勾「同时录制本地麦克风」（会议 App 的输出里没有你自己）。
5. 点「开始」。

## 控制协议（v1，冻结）

WebSocket 仅绑定 `127.0.0.1:17642`。文本帧 = JSON（每条带 `type`，可选 `ref` 透传回带）；
二进制帧 = Float32 LE、单声道、16kHz（`started.audioFormat` 上报），约 1600 样本/帧（100ms）。

| 方向 | 消息 | 说明 |
|---|---|---|
| C→S | `{"type":"ping"}` | 心跳 |
| C→S | `{"type":"requestPermission"}` | 触发系统授权弹窗并等待结果 |
| C→S | `{"type":"listApps"}` | 拉取可采集应用列表 |
| C→S | `{"type":"start","mode":"apps","bundleIds":[...]}` | 按应用采集（bundleId 合同，容忍应用重启） |
| C→S | `{"type":"start","mode":"system"}` | 整个系统音频 |
| C→S | `{"type":"stop"}` | 停止采集 |
| S→C | `hello{version,permission,mock}` | 握手完成即发 |
| S→C | `pong` / `permission{granted,effective}` | `effective=false` 表示需重启进程 |
| S→C | `apps[{pid,bundleId,name}]` | 按名称排序、去重 |
| S→C | `started{mode,sources,audioFormat{sampleRate,channels}}` | |
| S→C | `stopped{reason,message}` | reason: `request` / `client-disconnected` / `stream-error` / `app-exited` |
| S→C | `error{code,message}` | code: `no-permission` / `busy` / `invalid-params` / `start-failed` |
| S→C | `stats{framesSent,framesDropped}` | 采集中每 10s |

行为约定：

- **last-wins**：新页面连接后踢掉旧连接（页面刷新/双开不死锁）。
- **无客户端立即停止采集**：所有连接断开时停止 ScreenCaptureKit（屏幕录制指示器不空亮）。
- **背压**：每连接 8 帧（800ms）在途上限，超限丢帧并计入 `framesDropped`。
- 服务端每 30s 无出站流量时发一条 JSON `ping`，客户端应回 `pong`。
- 浏览器发来的二进制帧一律忽略。

## 安全模型

- 只监听回环地址，不暴露到网络。
- WebSocket 握手时校验 HTTP `Origin` 头：默认只放行 `http(s)://localhost|127.0.0.1|[::1]`（任意端口，
  即 meetingEZ 页面）。**Origin 缺失默认拒绝**——防止恶意网页连本机端口窃听音频。
  需要放开时用 `--allow-origin` 显式指定。
- 采集器不持有任何 OpenAI 凭据，也不接触 meetingEZ 后端；浏览器仍只拿短期 client secret。

## ws-probe（协议层测试客户端）

同二进制的隐藏子命令，用于无浏览器验证（详见 `ws-probe --help`）：

```bash
meetingez-capture ws-probe ping --origin http://localhost:5090       # 握手+pong
meetingez-capture ws-probe list-apps --origin http://localhost:5090  # 应用列表
meetingez-capture ws-probe start-system --duration 8 --origin http://localhost:5090
# 输出 frames/bytes/rms/maxAbs；配合 afplay / say 可验证真实音频链路
meetingez-capture ws-probe ping                                      # 无 Origin：应被拒绝
```

注：probe 客户端用 BSD socket 手写 WebSocket——Network.framework 的 NWProtocolWebSocket
客户端在部分 macOS 版本上（实测 26.5，裸 CLI 二进制）握手前直接 abort，服务端不受影响。

## 已知问题

- **macOS 14.0–14.x 按应用过滤音频存在采到全系统音频的报告**（OS 级缺陷）。14 后期版本与
  macOS 15+ 正常；若怀疑命中，用"静音其他应用 + stats 对比"诊断。
- 纯音频流按 2×2 视频维度 + 仅注册 `.audio` 输出实现；若个别系统版本 started 后无帧，
  降级方案是同时注册 `.screen` 输出丢弃（`SystemAudioTap` 内开关）。
- 采集静音的应用时 ScreenCaptureKit 可能不产帧，属正常现象（音频无内容）。

## 源码结构

```
Sources/MeetingEZCapture/
  main.swift              CLI 解析与模式分发（GUI / --no-gui / ws-probe）
  AppDelegate.swift       菜单栏：状态、重启（权限生效）、退出
  CaptureServer.swift     NWListener + NWProtocolWebSocket + Origin 校验 + last-wins + 背压
  CollectorService.swift  命令路由、权限轮询、统计推送（协议中枢）
  SystemAudioTap.swift    ScreenCaptureKit 封装：权限、应用枚举、流生命周期、错误恢复
  AudioFrameAggregator.swift  CMSampleBuffer → 16k mono Float32 定长帧（含格式兜底转换）
  MockAudioSource.swift   --mock-audio 假源
  WsProbe.swift           协议层测试客户端（BSD socket 手写 WS）
  Support.swift           协议合同、共享类型
```

线程模型：`serverQueue`（连接/收发）、`serviceQueue`（命令路由）、`sckQueue`（SCStream
生命周期）、`captureQueue`（音频样本处理）、主线程（菜单栏）；跨队列只传值类型。
