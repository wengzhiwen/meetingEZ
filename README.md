# MeetingEZ - 智能会议助手

轻量、开箱即用的实时会议转写与按需翻译工具。日本工匠级手搓轮子。

浏览器端直连 OpenAI 转写（gpt-4o-transcribe），并用 GPT-4.1-mini 做最小化后置处理。

使得在双语会议的环境中，你既可以查看对方的原话，同时还能看到母语翻译，最后还能在会议结束后将所有内容下载成txt文件拿去整理会议纪要。

## 亮点
- 实时：并行上传，低延迟显示（实际延迟大概10秒）
- 简单：纯前端直连 OpenAI，无服务端中转
- 准确：48kHz/16-bit 单声道采集，启发式去重与合并
- 明确：原文保留，第二语言自动在下一行插入第一语言的翻译
- 可用：记录本地保存，支持下载与一键清空

## 闪电开始

**在线体验**: [https://mez.cyberdoc.work/](https://mez.cyberdoc.work/)

你的openai API KEY只会保存在你浏览器本地，**不会**被发到上述服务器中。这个手搓的轮子不会有任何用户数据流经服务器。

## 快速开始
```bash
source venv/bin/activate
pip install -r requirements.txt
python run.py
```
打开浏览器，输入 OpenAI API Key，点击"开始会议"。

---

## 会议纪要整理 Agent

本项目还包含一个命令行自动化工具，用于**离线会议录音的转写与项目记忆管理**。

### 核心功能

- **自动转写** - 扫描会议录音目录，自动完成 ASR（支持长音频分块、断点续传）
- **智能纪要** - 使用 GPT-5.4 生成结构化会议纪要，支持多种会议类型
- **项目记忆** - 持续更新的项目上下文、时间线、待办追踪
- **智能提醒** - 会议前自动生成提示清单，发现超期待办和遗漏事项

### 快速开始

1. **准备会议目录**

```bash
mkdir -p meetings/2025-03-18_产品评审会
```

2. **创建会议元信息** (`_meeting.json`)

```json
{
  "date": "2025-03-18",
  "title": "产品评审会",
  "participants": ["张三", "李四"],
  "notes": "重点关注 Q2 路线图"
}
```

3. **放入录音文件**

将 `recording.m4a` 等音频文件放入会议目录。

4. **运行 Agent**

```bash
source venv/bin/activate
python -m meeting_agent run
```

### 产出物

Agent 会自动生成和更新以下文件：

| 文件 | 说明 |
|------|------|
| `transcript.json` | ASR 转写结果 |
| `minutes.md` | 会议纪要 |
| `timeline.md` | 项目时间线（关键节点、决策） |
| `actions.md` | 待办追踪（Action Items 状态） |
| `context.md` | 项目上下文（持续更新的"记忆"） |
| `pre_meeting_hint.md` | 会议前智能提示 |

### 命令速查

```bash
python -m meeting_agent run                    # 处理所有待处理会议
python -m meeting_agent run --watch            # 持续监控模式
python -m meeting_agent status                 # 查看项目状态
python -m meeting_agent actions --overdue      # 查看超期待办
```

### 详细文档

完整设计文档见：**[docs/meeting_minutes_agent.md](docs/meeting_minutes_agent.md)**

---

## 许可证
MIT（见 LICENSE）
