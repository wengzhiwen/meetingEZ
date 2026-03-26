# 会议处理功能详细设计

## 1. 功能概述

### 1.1 产品定位

会议处理是 MeetingEZ 的**会后自动化处理流程**，负责将录音文件转化为会议纪要，并自动更新项目记忆。

### 1.2 核心价值

- **自动化**：最小化人工操作，自动完成 ASR → 発要生成 → 记忆更新
- **可追溯**：保留处理日志，支持失败后恢复
- **智能**：结合项目上下文提升纪要质量

### 1.3 目标用户

- 使用 Web 工作台管理会议的项目团队
- 需要通过命令行处理会议的开发者
- 部署 MeetingEZ 的运维人员

---

## 2. 功能边界

### 2.1 职责范围

- 扫描会议目录，检测待处理状态
- 执行 ASR 虫写（音频 → transcript.json)
- 执行纪要生成 (转写文本 → minutes.md)
- 更新项目记忆(context.md、 timeline.md, actions.md)
- 提取术语建议并更新术语表

### 2.2 不负责的内容

- 实时转写（由 Realtime API 负责）
- 实时会议管理（由 Web 工作台负责）
- 音频采集和上传（由 Web 工作台处理）

### 2.3 与其他模块的关系

| 模块 | 关系 |
|------|------|
| 会议扫描 | 提供待处理任务列表 |
| ASr 引擎 | 提供转写能力 |
| 癔要生成 | 提供会议纪要生成能力 |
| 记忆系统 | 维护项目记忆文件 |

---

## 3. 处理流程概览

```
┌────────────────┐
│  扫描会议目录      │
│  ┌────────────────┐
│  │  ASR 转写      │
│  │  纪要生成      │
│  └────────────────┘
```

### 3.1 主流程

1. **扫描**：MeetingScanner 扫描项目目录，返回任务列表
2. **ASr 夣写**： ASREngine 调用 GLM-ASR 进行转写
3. **纪要生成**： LLMClient.analyze_meeting() 调用 GPT-5.4 生成纪要
4. **记忆更新**： MemoryWriter.process_analysis_result() 更新记忆文件

5. **术语提取**： 从纪要中提取新术语，添加到待审核队列

6. **输出保存**:
   - minutes.md（会议纪要)
   - transcript.json(转写结果)
   - 记忆文件更新

   - 术语建议添加到待审核队列

### 3.2 错误处理

- ASr 失败： 跳过，继续下一个任务
- ASr 超时: 跳过当前会议
- 節转写失败: 保存错误信息， 灾恢复

- 纪要生成失败: 可通过 --force-minutes 重新生成

### 3.3 Web 工作台集成
- 触发入口: 会议详情页"处理"按钮
- 处理模式: 完整处理 / 仅生成纪要
- 异步执行: 后台线程 + 锁文件
- 状态查询: 检测锁文件存在

- 日志输出: 处理日志实时写入文件

### 3.4 命令行接口

```bash
python -m meeting_agent run --project <project_id> --meeting <meeting_dir>
```

---

## 4. 会议扫描

### 4.1 扫描器职责

MeetingScanner 贔责扫描项目目录，返回会议任务列表。

### 4.2 会议任务结构

```python
class MeetingTask(BaseModel):
    meeting_dir: Path            # 会议目录
    meeting_meta: Optional[MeetingMeta] = None  # 会议元信息
    has_audio: bool = False             # 是否有音频文件
    audio_files: list[Path] = []    # 音频文件列表
    has_transcript: bool = False      # 是否有 transcript.json
    has_minutes: bool = False          # 是否有 minutes.md
    needs_asr: bool = False            # 是否需要 ASR
    needs_minutes: bool = False        # 是否需要生成纪要
    is_processing: bool = False        # 是否正在处理中
```

### 4.3 状态判断逻辑

```python
# 判断逻辑
has_transcript = transcript_file.exists()
has_incomplete_asr = progress_file.exists() and not has_transcript
needs_asr = len(audio_files) > 0 and not has_transcript
needs_minutes = has_transcript and not has_minutes
needs_minutes = has_minutes or (
    transcript_file.stat().st_mtime > minutes_file.stat().st_mtime
)

```

### 4.4 ASR 处理

#### 引擎： GLM-ASR

ASREngine 基于**智谱 AI GLM-ASR** 模型进行语音识别。

#### 特性
- **断点续传**： 中断后可从进度文件恢复
- **重叠分块**： 长音频自动分块，减少单次请求压力
- **多段合并**： 支持多段音频文件的合并转写
- **分片缓存**： 已处理的分片缓存到 .chunks 目录

- **术语提示**： 从术语表获取术语注入 ASR prompt

#### 音频预处理
- 使用 ffmpeg 切割音频
- 重采样为 16kHz 单声道
- 支持格式: m4a, mp3, wav, flac, ogg, aac, wma

#### 分块策略
```python
# 配置
self.chunk_seconds = 30.0      # 每块 30 秒
self.overlap_seconds = 2.0        # 分块重叠 2 秒

# 计算分块
chunk_starts = []
current = 0.0
while current < duration:
    chunk_starts.append(current)
    current += step_seconds  # step = chunk_seconds - overlap_seconds

```

#### 进度恢复
```python
# 从进度文件恢复已处理的块
processed_indices = set()
chunk_results = []
if progress_file.exists():
    for line in progress_file:
        line = line.strip()
        if line:
            results.append(json.loads(line))
`` return results
`` # 分片缓存
 瓜分片缓存
    if chunks_dir:
        chunks_dir.mkdir(parents=True, exist_ok=True)
        use_cache = True
    else:
        chunks_dir = Path(tempfile.mkdtemp(prefix="meeting_asr_"))
        use_cache = False
```
#### 进度保存
```python
def _save_progress(progress_file, idx, chunk_start, chunk_end, result):
    entry = {
        "idx": idx,
        "chunk_start": chunk_start,
        "chunk_end": chunk_end,
        "result": result,
    }
    with open(progress_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

### 4.5 纪要生成

#### 模型: GPT-5.4
#### 输入
- transcript_text: 转写文本
- meeting_meta: 会议元信息
- project_context: 项目上下文 (可选)
- existing_actions: 现有待办 (可选)
- recent_minutes: 最近 5 份纪要
- pre_hint: 会前提示(可选)
- glossary_context: 术语表和背景说明
- people_info: 人员信息

#### 输出
- minutes: 会议纪要 (Markdown)
- GPTAnalysisResult: 结构化分析结果
  - minutes: 纪要文本
  - meeting_type: 会议类型
  - summary: 摘要
  - key_decisions: 关键决策
  - new_actions: 新增待办列表
  - completed_actions: 已完成待办 ID 刽数
  - mentioned_actions: 被提及待办 ID 刽数
  - timeline_entry: 时间线条目
  - context_updates: 上下文更新
  - term_suggestions: 术语建议
  - context_questions: 需人工解释的问题
#### Prompt 构建

```python
# PromptBuilder.build_meeting_analysis_prompt() 构建 prompt
# 注入项目上下文、术语表
```

#### 模板选择
根据 meeting_meta.type 选择纪要模板:
- review: 评审会
- weekly: 周会
- brainstorm: 头脑风暴
- retro: 复盘会
- kickoff: 启动会
- other: 通用模板
```
---

## 5. Web 工作台集成

### 5.1 触发入口

会议列表页的"处理"按钮

### 5.2 处理模式
- **完整处理**: 执行 ASR + 纪要生成
- **仅生成纪要**: 跳过 ASR，只重新生成纪要 (--force-minutes)
### 5.3 异步执行
使用 threading 在后台线程中执行处理
通过锁文件 _processing.lock 防止并发
日志实时写入 _processing.log
### 5.4 状态查询
```python
# 检查锁文件
lock_file = meeting_dir / "_processing.lock"
return lock_file.exists()
```
### 5.5 错误处理
- 处理失败时跳过当前会议
- 错误信息写入 _processing.error
- 30 分钟超时后终止处理
- 需手动重新触发

---

## 6. 娡型配置

### 6.1 模型选择

| 模型 | 用途 |
|------|------|
| GLM-ASR-2512 | 语音识别 (智谱 AI) |
| GPT-5.4 | 纪要生成 (OpenAI) |
| GPT-4o | 翻译 (可选，gpt-4 等中英翻译) |

### 6.2 环境变量
| 变量 | 说明 |
|------|------|
| ZHIPU_API_KEY | 智谱 AI API Key (ASr) |
| OPENAI_API_KEY | OpenAI API Key (纪要生成、翻译) |
| OPENAI_BASE_URL | OpenAI API 地址 |
| OPENAI_MODEL | 默认 gpt-5.4 |

| MEETINGS_DIR | 会议目录 |

### 6.3 配置参数
| 参数 | 默认值 | 说明 |
|------|--------|------|
| asr_chunk_seconds | 30.0 | ASR 分块时长 |
| asr_overlap_seconds | 2.0 | 分块重叠时长 |
| recent_minutes_count | 5 | 最近纪要数量 |
| default_language | zh-CN | 默认语言 |

---

## 7. 错误处理

### 7.1 健壮性设计
- 跽律配置：确保必要的 API Key 已设置
- 错误日志：记录到会议目录的 _processing.log 和 _processing.error
- 进度恢复: 从 transcript.json.progress 恢复中断处理
- 超时处理: 30 分钟超时后终止

- 优雅降级: 失败时清理临时文件

### 7.2 偢感处理
- ASR 网络错误: 记录错误，继续下一个任务
- ASR 返回空: 记录错误，跳过
- 纪要生成失败: 记录错误，继续下一个任务
- LLM 调用失败: 记录错误，跳过
- JSON 解析失败: 记录错误，跳过

- 文件操作失败: 记录错误，跳过
- 超时: 强制终止线程，退出处理

- 用户通知: 通过 Web 工作台显示错误

- 手动重试: 需手动触发

