# 翻译功能详细设计

## 1. 功能概述

### 1.1 产品定位

翻译功能是 MeetingEZ 实时会议场景的**后置语言处理模块**，在实时转写完成后，为字幕提供双向翻译能力。

### 1.2 核心价值

- **跨语言沟通**：帮助使用不同语言的参会者理解会议内容
- **术语一致性**：通过术语表增强，确保专有名词翻译准确
- **智能修正**：在允许时对 ASR 结果进行轻量修正，提升字幕质量

### 1.3 目标用户

- 参与双语会议的参会者
- 需要理解外语发言的用户
- 对字幕翻译质量有要求的会议组织者

---

## 2. 功能边界

### 2.1 职责范围

- 接收 ASR 完成的实时转写文本
- 判定原文语言
- 执行智能修正（可选)
- 输出双向翻译结果

### 2.2 不负责的内容
- 实时转写过程(由 OpenAI Realtime API 负责)
- 会议纪要生成(由 Agent 负责)
- 项目记忆更新(由 Agent 负责)

### 2.3 与其他模块的关系
| 模块 | 关系 |
|------|------|
| 实时转写 | 提供 ASR 原始文本作为输入 |
| 术语表 | 提供术语信息用于翻译增强 |
| Web 工作台 | 提供配置界面和查看翻译历史 |

---

## 3. 语言模式

### 3.1 单主语言模式 (single_primary)

- **定义**: 会议主要使用一种语言，偶尔夹杂少量外语术语或缩写
- **特点**:
  - 主要语言为主，外语术语保持原样
  - 不将外语句子误判为第二语言
  - 适用于大多数国内会议场景

### 3.2 双语言模式 (bilingual)

- **定义**: 两种语言都是正式的会议语言
- **特点**:
  - 保持原始语言边界
  - 不混淆不同语言
  - 适用于国际会议、跨语言团队协作

### 3.3 语言角色

| 角色 | 说明 |
|------|------|
| primary_language | 主要语言（如中文 zh-CN） |
| secondary_language | 第二语言(如日语 ja) |
| original_language | 当前文本的实际语言(由系统判定) |

---

## 4. 翻译触发机制

### 4.1 触发时机

翻译在**实时转写 completed 事件**后触发。

```javascript
// realtime-transcription.js 中的事件处理
function handleTranscriptCompleted(item) {
    // 检查是否配置了第二语言
    if (!state.secondary_language) return;

    // 触发翻译
    translateItem(item);
}
```

### 4.2 前端判断逻辑

```javascript
// 判断是否需要翻译
function shouldTranslate() {
    // 双语言模式下，需要配置了第二语言才翻译
    return state.language_mode === 'bilingual' && state.secondary_language;
}
```

### 4.3 API 调用

```javascript
// 调用 /api/translate 掱async function translateItem(item) {
    const response = await fetch('/api/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            text: item.text,
            primaryLanguage: state.primary_language,
            secondaryLanguage: state.secondary_language,
            languageMode: state.language_mode,
            // 可选参数
            enableCorrection: enableCorrection,
            enableGlossary: enableGlossary,
            glossary: glossaryEntries,
            context: recentContext,
            meetingContext: meetingContext
        })
    });

    if (response.ok) {
        const result = await response.json();
        // 更新 UI
        updateTranscriptWithTranslation(item.item_id, result);
    }
}
```

---

## 5. 翻译处理流程

### 5.1 完整流程

```
┌──────────────────────────────────────────────────────────────────┐
│                    ASR 原始文本                               │
│                         (current_text)                                │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│                     语言检测与判定                              │
│                   (originalLanguage)                               │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│                    智能修正 (可选)                              │
│                 (correctedTranscript)                               │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│                      双向翻译                                │
│            (primaryTranslation / secondaryTranslation)            │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│                     结果返回给前端                            │
│                    更新字幕显示                               │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 语言检测

- **输入**: ASR 原始文本
- **处理**: LLM 根据文本内容自动判断语言
- **输出**: ISO 639-1 语言代码 (zh, en, ja, ko, es, fr, de, ru, pt 等)

### 5.3 智能修正

#### 触发条件
- `enableCorrection=true`
- 由前端设置控制

- 默认关闭

#### 修正范围
- **ASR 错误修正**: 语音识别产生的明显错误
- **术语对齐**: 将术语表中的别名映射为标准写法
- **标点断句**: 轻度调整标点和断句
- **约束**:
  - 不改写说话意图
  - 不补充未说出的信息
  - 不用总结替代原句
  - 没有明确证据时不擅自修改

#### 术语表增强
当 `enableGlossary=true` 时:
- 优先将术语修正为 glossary_entries 中的标准写法
- 使用术语表提供的别名进行匹配

### 5.4 双向翻译规则

#### 翻译方向判定

| 原文语言 | primaryTranslation | secondaryTranslation |
|----------|---------------------|----------------------|
| 第一语言 (primary) | null | 翻译为第二语言 |
| 第二语言 (secondary) | 翻译为第一语言 | null |
| 其他语言 | 翻译为第一语言 | null |

#### 禁止同语种翻译
- 如果目标语言与原文同语种，对应字段必须为 null
- `originalLanguage` 必须是判定的原文语言，而非目标语言

---

## 6. API 接口

### 6.1 请求格式

```http
POST /api/translate
Content-Type: application/json

{
    "text": "ASR 原始文本",
    "primaryLanguage": "zh",
    "secondaryLanguage": "ja",
    "languageMode": "bilingual",
    "originalLanguageHint": "zh",
    "enableCorrection": false,
    "enableGlossary": true,
    "glossary": "OpenAI|Realtime API\nGLM-ASR",
    "context": "最近上下文...",
    "meetingContext": "会议上下文..."
}
```

### 6.2 响应格式

```json
{
    "originalLanguage": "zh",
    "correctedTranscript": null,
    "correctionApplied": false,
    "primaryTranslation": null,
    "secondaryTranslation": "这是翻译成日语的文本"
}
```

### 6.3 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| text | string | 是 | ASR 原始文本 |
| primaryLanguage | string | 是 | 主要语言代码 |
| secondaryLanguage | string | 否 | 第二语言代码 |
| languageMode | string | 是 | 语言模式 |
| originalLanguageHint | string | 否 | 原文语言提示 |
| enableCorrection | boolean | 否 | 是否启用智能修正 |
| enableGlossary | boolean | 否 | 是否启用术语表 |
| glossary | string | 否 | 术语表(管道分隔) |
| context | string | 否 | 最近上下文 |
| meetingContext | string | 否 | 会议级上下文 |

### 6.4 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| originalLanguage | string | 判定的原文语言 |
| correctedTranscript | string \| null | 修正后的文本(未启用则为 null) |
| correctionApplied | boolean | 是否真的修改了原文 |
| primaryTranslation | string \| null | 翻译为第一语言的结果 |
| secondaryTranslation | string \| null | 翻译为第二语言的结果 |

---

## 7. 前端展示逻辑

### 7.1 字幕显示流程

```
1. 原文先显示
   ↓
2. 翻译请求发送
   ↓
3. 翻译完成后，在原文后插入翻译行
   ↓
4. 翻译行样式区分(缩进、颜色)
```

### 7.2 上下文传递

为保持翻译连贯性，前端传递最近 N 条原文+翻译:

```javascript
// 构建上下文
const recentContext = recentTranscripts
    .slice(-3)  // 最近 3 条
    .map(t => `${t.text}\n${t.translation || ''}`)
    .join('\n\n');
```

### 7.3 UI 更新逻辑

```javascript
function updateTranscriptWithTranslation(itemId, result) {
    const item = state.transcripts.find(t => t.item_id === itemId);
    if (item) {
        // 更新翻译结果
        if (result.primaryTranslation) {
            item.primary_translation = result.primaryTranslation;
        }
        if (result.secondaryTranslation) {
            item.secondary_translation = result.secondaryTranslation;
        }
        // 重新渲染字幕
        renderTranscripts();
    }
}
```

---

## 8. 配置项

### 8.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| TRANSLATION_MODEL | gpt-4o-transcribe | 翻译使用的模型 |
| TRANSLATION_REASONING_EFFORT | low | 推理强度 (low/medium/high) |

### 8.2 模型能力

- **gpt-5 系列**: 支持 reasoning.effort 参数
- **gpt-4o 系列**: 不支持 reasoning 参数

```python
def _supports_translation_reasoning(model):
    """只在明确支持 reasoning 的模型上发送 reasoning 参数"""
    normalized_model = (model or '').strip().lower()
    return normalized_model.startswith('gpt-5')
```

### 8.3 前端设置

| 设置 | 默认值 | 说明 |
|------|--------|------|
| 语言模式 | single_primary | 单主语言/双语言 |
| 主要语言 | zh-CN | 主要语言代码 |
| 第二语言 | - | 第二语言代码(双语言模式必需) |
| 智能修正 | 关闭 | 是否启用智能修正 |
| 术语表增强 | 开启 | 是否使用术语表 |

---

## 9. 性能考量

### 9.1 延迟目标

- 翻译延迟 < 1 秒
- 总体延迟(ASR + 翻译) < 3 秒

### 9.2 并发控制

- 翻译请求顺序处理，避免竞态
- 使用队列管理待翻译项目

### 9.3 上下文长度

- 最近 3-5 条原文+翻译
- 上下文总长度控制在 2000 字符以内

---

## 10. 错误处理

### 10.1 常见错误

| 错误 | 处理方式 |
|------|----------|
| API Key 未配置 | 返回 500 错误 |
| 翻译请求超时 | 30 秒超时，显示原文 |
| JSON 解析失败 | 显示原文，记录错误日志 |
| 网络错误 | 重试 1 次，失败后显示原文 |

### 10.2 降级策略

- 翻译失败时，仅显示原文
- 不影响实时转写的继续进行
- 错误信息记录到控制台

---

## 11. 未来规划

### 11.1 已知限制

1. 仅支持文本翻译，不支持实时语音翻译
2. 翻译延迟可能影响用户体验
3. 术语表依赖人工维护

### 11.2 待优化项

1. 支持更多语言对
2. 翻译缓存机制
3. 流式翻译(边接收边翻译)

### 11.3 扩展方向

1. **专业术语词典**: 领域专用翻译词典
2. **个性化翻译**: 根据用户偏好调整翻译风格
3. **多语言支持**: 支持三种以上语言的会议
