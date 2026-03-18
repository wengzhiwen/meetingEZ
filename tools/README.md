# 智谱AI 云端 ASR 转录工具

独立命令行工具，使用智谱AI GLM-ASR-2512 模型进行音频转写。

## 特性

- 支持多种音频格式（m4a、mp3、wav 等 ffmpeg 支持的格式）
- 长音频自动分块处理（30秒/块，2秒重叠）
- 多种输出格式（纯文本、带时间戳文本、JSON、SRT字幕）
- 断点续传支持（进度文件）

## 快速开始

```bash
# 设置 API Key（在 .env 文件中或环境变量）
export ZHIPU_API_KEY="your_api_key"

# 转录音频
./zhipu_asr.sh input.m4a

# 指定输出文件和格式
./zhipu_asr.sh input.mp3 -o result.txt -f json

# 直接使用 Python
./venv/bin/python tools/zhipu_asr.py input.wav --api-key YOUR_API_KEY
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `input` | 输入音频文件 | (必需) |
| `-o, --output` | 输出文件路径 | (stdout) |
| `-f, --format` | 输出格式: text/text_with_time/json/srt | text |
| `--model` | 模型名称 | glm-asr-2512 |
| `--api-key` | 智谱AI API Key | (从环境变量读取) |
| `--api-base-url` | API 地址 | https://open.bigmodel.cn/api/paas/v4 |
| `--debug-first-chunk` | 仅处理第一个切片（调试用） | false |

## 输出格式示例

### text（纯文本）
```
第一句话
第二句话
第三句话
```

### text_with_time（带时间戳）
```
[0.00 - 2.35] 第一句话
[2.35 - 5.12] 第二句话
[5.12 - 8.45] 第三句话
```

### json
```json
[
  {"start": 0.0, "end": 2.35, "text": "第一句话"},
  {"start": 2.35, "end": 5.12, "text": "第二句话"},
  {"start": 5.12, "end": 8.45, "text": "第三句话"}
]
```

### srt（字幕格式）
```
1
00:00:00,000 --> 00:00:02,350
第一句话

2
00:00:02,350 --> 00:00:05,120
第二句话
```

## 依赖

- Python 3.8+
- ffmpeg（用于音频切割）
- requests
- python-dotenv（可选，用于加载 .env 文件）
