# MeetingEZ - 智能会议助手

轻量、开箱即用的实时会议转写与按需翻译工具。浏览器端分段直连 OpenAI 转写（gpt-4o-transcribe），并用 Responses 做最小化后置处理（语言判定与在非第一语言时插入第一语言的翻译）。

## 亮点
- 实时：并行上传，低延迟显示（实际延迟大概10秒）
- 简单：纯前端直连 OpenAI，无服务端中转
- 准确：48kHz/16-bit 单声道采集，启发式去重与合并
- 明确：原文保留，第二语言自动在下一行插入第一语言的翻译
- 可用：记录本地保存，支持下载与一键清空

## 快速开始
```bash
source venv/bin/activate
pip install -r requirements.txt
python run.py
```
打开浏览器，输入 OpenAI API Key，点击“开始会议”。


## 许可证
MIT（见 LICENSE）

