# 术语表系统功能详细设计

## 1. 功能概述

### 1.1 产品定位

术语表系统是 MeetingEZ 的核心增强能力，用于维护项目级术语知识库。通过规范化术语写法，提升 ASR 识别精度、翻译准确性和纪要一致性。

### 1.2 核心价值

- **提升识别精度**：将术语注入 ASR 提示词，减少误识别
- **规范术语写法**：统一项目中的专业术语、人名、产品名
- **支持翻译对齐**：双语会议中的术语翻译一致性
- **持续积累**：从会议中自动提取，人工确认后沉淀

### 1.3 目标用户

- 需要管理项目术语的项目经理
- 需要审核术语建议的团队成员
- 需要查看术语定义的新成员

---

## 2. 功能边界

### 2.1 职责范围

| 功能 | 是否负责 | 说明 |
|------|----------|------|
| 术语存储 | ✅ | 三态存储：已确认/待审核/已拒绝 |
| 术语提取 | ✅ | Agent 从会议纪要中自动提取 |
| 术语审核 | ✅ | Web 工作台审核界面 |
| 术语应用 | ✅ | 注入 ASR/翻译/纪要生成 |
| 背景知识 | ❌ | 由背景说明功能负责 |
| 翻译词典 | ❌ | 术语不包含翻译映射 |

### 2.2 与其他功能的关系

```
┌─────────────────────────────────────────────────────────────┐
│                      术语表系统                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   输入来源                     输出应用                     │
│   ┌─────────────┐             ┌─────────────┐              │
│   │ 会议纪要    │ ──────────▶ │ 实时 ASR    │              │
│   │ (自动提取)  │             │ (prompt 注入)│              │
│   └─────────────┘             └─────────────┘              │
│   ┌─────────────┐             ┌─────────────┐              │
│   │ 手动添加    │ ──────────▶ │ 翻译后处理  │              │
│   │ (Web 工作台)│             │ (术语对齐)  │              │
│   └─────────────┘             └─────────────┘              │
│                               ┌─────────────┐              │
│                               │ 纪要生成    │              │
│                               │ (人名规范)  │              │
│                               └─────────────┘              │
│                               ┌─────────────┐              │
│                               │ GLM-ASR     │              │
│                               │ (术语提示)  │              │
│                               └─────────────┘              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 术语生命周期

### 3.1 状态定义

```
┌─────────────────────────────────────────────────────────────┐
│                     术语状态流转                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                    ┌─────────────┐                          │
│                    │   pending   │  ← 新增术语              │
│                    │   待审核    │    (Agent 提取/手动添加) │
│                    └─────────────┘                          │
│                     /           \                           │
│           [确认]   /             \   [拒绝]                 │
│                   ▼               ▼                         │
│          ┌─────────────┐   ┌─────────────┐                 │
│          │  confirmed  │   │  rejected   │                 │
│          │   已确认    │   │   已拒绝    │                 │
│          └─────────────┘   └─────────────┘                 │
│               │                   │                         │
│               │ [回退]            │ [回退]                  │
│               └───────┬───────────┘                         │
│                       ▼                                     │
│               ┌─────────────┐                               │
│               │   pending   │                               │
│               │   待审核    │                               │
│               └─────────────┘                               │
│                                                             │
│   删除: 从任意状态永久移除                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 状态说明

| 状态 | 文件 | 说明 | 用途 |
|------|------|------|------|
| `confirmed` | `_glossary.json` | 已确认的术语 | 参与所有增强 |
| `pending` | `_glossary_pending.json` | 待审核的术语 | 等待人工确认 |
| `rejected` | `_glossary_rejected.json` | 已拒绝的术语 | 防止重复提取 |

### 3.3 状态转换操作

| 操作 | 从 | 到 | 触发方式 |
|------|----|----|----|
| 确认 | pending | confirmed | Web 工作台点击"确认" |
| 拒绝 | pending | rejected | Web 工作台点击"拒绝" |
| 回退 | confirmed | pending | Web 工作台点击"↩ 待审核" |
| 回退 | rejected | pending | Web 工作台点击"↩ 待审核" |
| 删除 | confirmed | (移除) | 编辑模式下点击"删除" |

---

## 4. 数据结构

### 4.1 已确认术语（GlossaryEntry）

```python
class GlossaryEntry(BaseModel):
    canonical: str                          # 标准名称
    aliases: list[str] = []                 # 别名/常见错误识别
    type: TermType = TermType.OTHER         # 术语类型
    description: Optional[str] = None       # 简短描述
    context: Optional[str] = None           # 使用背景/上下文
    auto_generated: bool = True             # 是否自动生成
    confirmed_at: Optional[datetime] = None # 确认时间
    confirmed_by: Optional[str] = None      # 确认人
    source_meeting: Optional[str] = None    # 来源会议
```

### 4.2 待审核术语（TermSuggestion）

```python
class TermSuggestion(BaseModel):
    canonical: str                          # 标准名称
    aliases: list[str] = []                 # 别名
    type: TermType = TermType.OTHER         # 术语类型
    context: Optional[str] = None           # 出现的上下文
    frequency: int = 1                      # 出现频率（多次会议出现时累加）
    source_meeting: Optional[str] = None    # 来源会议
    suggested_at: datetime                  # 建议时间
```

### 4.3 已拒绝术语（RejectedTerm）

```python
class RejectedTerm(BaseModel):
    canonical: str                          # 标准名称
    aliases: list[str] = []                 # 别名
    type: TermType = TermType.OTHER         # 术语类型
    context: Optional[str] = None           # 上下文
    source_meeting: Optional[str] = None    # 来源会议
    reason: Optional[str] = None            # 拒绝原因
    rejected_at: datetime                   # 拒绝时间
    rejected_by: Optional[str] = None       # 拒绝人
```

### 4.4 术语类型（TermType）

```python
class TermType(str, Enum):
    PERSON = "person"           # 人名
    PRODUCT = "product"         # 产品名
    TECHNICAL = "technical"     # 技术术语
    PROJECT = "project"         # 项目特定术语
    ABBREVIATION = "abbr"       # 缩写
    OTHER = "other"             # 其他
```

### 4.5 存储文件结构

```
meetings/
├── _glossary.json              # 已确认术语
├── _glossary_pending.json      # 待审核术语
└── _glossary_rejected.json     # 已拒绝术语
```

#### _glossary.json 示例

```json
{
  "version": 1,
  "last_updated": "2026-03-25T10:00:00",
  "entries": [
    {
      "canonical": "MeetingEZ",
      "aliases": ["米听易", "meeting-ez", "meeting ez"],
      "type": "product",
      "description": "智能会议纪要系统",
      "context": "产品名称，用于会议转写和纪要生成",
      "auto_generated": true,
      "confirmed_at": "2026-03-20T14:00:00",
      "source_meeting": "2026-03-20 启动会"
    },
    {
      "canonical": "张三",
      "aliases": ["老张", "Zhang San"],
      "type": "person",
      "description": "技术负责人",
      "context": "团队成员，负责后端开发",
      "auto_generated": true,
      "confirmed_at": "2026-03-20T14:00:00",
      "source_meeting": "2026-03-20 启动会"
    }
  ]
}
```

---

## 5. 详细设计

### 5.1 术语提取流程

```
会议纪要生成完成
    │
    ▼
┌─────────────────────────────────────┐
│ LLM 分析会议纪要                    │
│ - 识别人名、产品名、技术术语        │
│ - 判断是否为新术语                  │
│ - 提取术语上下文                    │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 术语去重检查                        │
│ - 检查是否在 confirmed 表           │
│ - 检查是否在 rejected 表            │
│ - 检查是否已在 pending 表           │
└─────────────────────────────────────┘
    │
    │ 通过检查
    ▼
┌─────────────────────────────────────┐
│ 添加到 pending 表                   │
│ - 合并别名（如果已存在）            │
│ - 累加 frequency                    │
│ - 更新 source_meeting               │
└─────────────────────────────────────┘
```

### 5.2 术语审核流程

```
Web 工作台 → 术语 Tab
    │
    ▼
┌─────────────────────────────────────┐
│ 显示待审核术语列表                  │
│ - 按频率排序（高频优先）            │
│ - 显示来源会议                      │
│ - 显示上下文                        │
└─────────────────────────────────────┘
    │
    ├─────────────────────┬─────────────────────┐
    │                     │                     │
    ▼                     ▼                     ▼
[确认]                 [拒绝]              [忽略]
    │                     │                     │
    ▼                     ▼                     │
移入 confirmed       移入 rejected            (保持 pending)
记录确认时间         记录拒绝原因
```

### 5.3 术语应用场景

#### 5.3.1 实时 ASR 增强

```python
def build_realtime_prompt(glossary: Glossary) -> str:
    """构建注入到 Realtime Session 的 prompt"""
    entries = [e for e in glossary.entries if e.confirmed_at]

    if not entries:
        return ""

    # 简化格式，适合 prompt 注入
    lines = ["术语参考："]
    for entry in entries[:16]:  # 限制条目数
        if entry.aliases:
            lines.append(f"{entry.canonical}（{', '.join(entry.aliases[:3])}）")
        else:
            lines.append(entry.canonical)

    return "；".join(lines)
```

#### 5.3.2 翻译后置处理

```python
def build_correction_map(glossary: Glossary) -> dict[str, str]:
    """构建纠错映射：错误/别名 → 正确形式"""
    correction_map = {}
    for entry in glossary.entries:
        for alias in entry.aliases:
            correction_map[alias.lower()] = entry.canonical
    return correction_map

# 翻译时使用
# 如果 enableGlossary=true，将 correction_map 传递给翻译模型
# 模型会优先将文本修正为标准术语
```

#### 5.3.3 会后 ASR 提示

```python
def build_glossary_prompt(glossary: Glossary) -> str:
    """构建用于 GLM-ASR 的术语提示"""
    # 按类型分组
    by_type: dict[TermType, list[GlossaryEntry]] = {}
    for entry in glossary.entries:
        if entry.type not in by_type:
            by_type[entry.type] = []
        by_type[entry.type].append(entry)

    type_names = {
        TermType.PERSON: "人名",
        TermType.PRODUCT: "产品名",
        TermType.TECHNICAL: "技术术语",
        # ...
    }

    lines = ["## 术语表（用于修正识别错误）\n"]
    for term_type, entries in by_type.items():
        lines.append(f"### {type_names.get(term_type, '其他')}")
        for entry in entries:
            if entry.aliases:
                lines.append(f"- **{entry.canonical}**: 常见错误 → {', '.join(entry.aliases)}")
            else:
                lines.append(f"- **{entry.canonical}**")
        lines.append("")

    return "\n".join(lines)
```

### 5.4 术语合并策略

当同一术语从多个会议中提取时，采用合并策略：

```python
def add(self, suggestion: TermSuggestion) -> None:
    """添加建议（自动合并）"""
    # 检查是否已存在
    for existing in self.suggestions:
        if existing.canonical.lower() == suggestion.canonical.lower():
            # 合并别名
            for alias in suggestion.aliases:
                if alias.lower() not in [a.lower() for a in existing.aliases]:
                    existing.aliases.append(alias)
            # 累加频率
            existing.frequency += 1
            self.last_updated = datetime.now()
            return

    # 不存在则新增
    self.suggestions.append(suggestion)
    self.last_updated = datetime.now()
```

### 5.5 术语查找逻辑

```python
def _find_entry(self, canonical: str) -> tuple[int, GlossaryEntry] | None:
    """查找术语条目（大小写不敏感）"""
    for i, entry in enumerate(self.entries):
        if entry.canonical.lower() == canonical.lower():
            return (i, entry)
    return None
```

---

## 6. 与其他模块的交互

### 6.1 与会议处理 Agent 的交互

```
会议处理 Agent
    │
    │ 1. 生成会议纪要
    ▼
┌─────────────────────────────────────┐
│ LLM 分析纪要                        │
│ - 提取新术语                        │
│ - 识别现有术语                      │
└─────────────────────────────────────┘
    │
    │ 2. 提取术语建议
    ▼
GlossaryManager.suggest_term()
    │
    │ 3. 检查去重
    ▼
┌─────────────────────────────────────┐
│ - 检查 confirmed 表                 │
│ - 检查 rejected 表                  │
│ - 检查 pending 表                   │
└─────────────────────────────────────┘
    │
    │ 4. 添加到 pending 表
    ▼
保存 _glossary_pending.json
```

### 6.2 与实时转写的交互

```
实时转写页面
    │
    │ 1. 加载 Context Pack
    ▼
GET /api/workspace/context-pack
    │
    │ 2. 包含术语表
    ▼
┌─────────────────────────────────────┐
│ buildRealtimePrompt()               │
│ - 提取已确认术语                    │
│ - 限制条目数（≤16）                │
│ - 生成简化格式                      │
└─────────────────────────────────────┘
    │
    │ 3. 注入 Realtime Session
    ▼
POST /api/realtime-session
    │
    │ 4. 翻译时使用
    ▼
POST /api/translate (glossary 参数)
```

### 6.3 与 Web 工作台的交互

```
Web 工作台
    │
    │ 1. 进入术语 Tab
    ▼
GET /api/workspace/project/:id/glossary
    │
    │ 返回合并后的术语列表
    ▼
┌─────────────────────────────────────┐
│ 前端展示                            │
│ - confirmed: 绿色标签               │
│ - pending: 黄色标签 + 审核按钮      │
│ - rejected: 灰色标签 + 回退按钮     │
└─────────────────────────────────────┘
    │
    │ 2. 用户操作
    ▼
POST /api/workspace/project/:id/glossary/approve
POST /api/workspace/project/:id/glossary/reject
POST /api/workspace/project/:id/glossary/revert
```

---

## 7. API 接口（功能视角）

### 7.1 查询接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/workspace/project/:id/glossary` | GET | 获取术语列表（三态合并） |

返回格式：

```json
{
  "project": { "id": "...", "name": "..." },
  "terms": [
    {
      "state": "confirmed",
      "canonical": "MeetingEZ",
      "aliases": ["米听易"],
      "type": "product",
      "context": "产品名称",
      "source_meeting": "2026-03-20 启动会"
    },
    {
      "state": "pending",
      "canonical": "API Key",
      "aliases": [],
      "type": "technical",
      "context": "认证凭证",
      "frequency": 5,
      "source_meeting": "2026-03-20 启动会"
    }
  ]
}
```

### 7.2 术语操作接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/workspace/project/:id/glossary/entries` | POST | 添加术语 |
| `/api/workspace/project/:id/glossary/entries/:canonical` | PUT | 更新术语 |
| `/api/workspace/project/:id/glossary/entries/:canonical` | DELETE | 删除术语 |

### 7.3 审核操作接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/workspace/project/:id/glossary/approve` | POST | 确认术语 |
| `/api/workspace/project/:id/glossary/reject` | POST | 拒绝术语 |
| `/api/workspace/project/:id/glossary/revert` | POST | 回退术语 |

请求体示例：

```json
// approve
{ "canonical": "API Key" }

// reject
{ "canonical": "API Key", "reason": "通用词汇，不需要收录" }

// revert
{ "canonical": "API Key", "from_state": "confirmed" }
```

---

## 8. 配置项

### 8.1 术语类型配置

```python
class TermType(str, Enum):
    PERSON = "person"           # 人名
    PRODUCT = "product"         # 产品名
    TECHNICAL = "technical"     # 技术术语
    PROJECT = "project"         # 项目特定术语
    ABBREVIATION = "abbr"       # 缩写
    OTHER = "other"             # 其他

# 前端显示映射
TYPE_LABELS = {
    "person": "人名",
    "product": "产品",
    "technical": "技术",
    "project": "项目",
    "abbr": "缩写",
    "other": "其他"
}
```

### 8.2 术语限制

| 限制项 | 值 | 说明 |
|--------|-----|------|
| Prompt 条目上限 | 16 | 实时 ASR 注入时限制 |
| 别名数量上限 | 无限制 | 但建议 ≤ 5 个 |
| 术语总数量 | 无限制 | 按项目积累 |

---

## 9. 错误处理

### 9.1 常见错误

| 错误类型 | 原因 | 处理方式 |
|----------|------|----------|
| 术语已存在 | 尝试添加已确认的术语 | 静默忽略或提示 |
| 术语不存在 | 操作不存在的术语 | 返回 404 |
| 重复拒绝 | 拒绝已拒绝的术语 | 静默忽略 |

### 9.2 去重保护

```python
def suggest_term(self, canonical: str, ...) -> Optional[TermSuggestion]:
    """添加术语建议（带去重保护）"""
    # 检查是否已在已接受术语表中
    if self.load_glossary().get_entry(canonical):
        logger.debug("术语已在术语表中，跳过: %s", canonical)
        return None

    # 检查是否已被拒绝
    if self.load_rejected().is_rejected(canonical):
        logger.debug("术语已被拒绝，跳过: %s", canonical)
        return None

    # 添加到待审核
    # ...
```

---

## 10. 性能考量

### 10.1 查找优化

- 使用大小写不敏感的查找
- 使用字典映射加速别名查找

```python
def get_all_aliases(self) -> dict[str, str]:
    """获取所有别名到标准名称的映射"""
    mapping = {}
    for entry in self.entries:
        for alias in entry.aliases:
            mapping[alias.lower()] = entry.canonical
        mapping[entry.canonical.lower()] = entry.canonical
    return mapping
```

### 10.2 文件读写

- 内存缓存已加载的术语表
- 仅在修改时写入文件

```python
def load_glossary(self) -> Glossary:
    if self._glossary is not None:
        return self._glossary  # 返回缓存
    # 从文件加载...
```

---

## 11. 与背景说明的关系

### 11.1 功能边界

| 功能 | 术语表 | 背景说明 |
|------|--------|----------|
| 存储内容 | 短词、固定写法 | 长文本、概念解释 |
| 用途 | ASR 修正、翻译对齐 | LLM 上下文理解 |
| 格式 | 结构化 JSON | 问答对形式 |
| 来源 | 自动提取 + 手动 | 完全手动 |

### 11.2 组合使用

```python
def get_combined_context(config: Config) -> str:
    """组合术语表 + 背景说明"""
    parts = []

    # 1. 术语表
    glossary = GlossaryManager(config)
    parts.append(glossary.build_glossary_prompt())

    # 2. 背景说明
    background = BackgroundContextManager(config)
    parts.append(background.to_prompt_text())

    return "\n\n".join(parts)
```

---

## 12. 未来规划

### 12.1 已知限制

1. **无翻译映射**：术语不包含双语翻译
2. **无使用统计**：不跟踪术语在会议中的使用次数
3. **无批量操作**：无法批量确认/拒绝术语
4. **无导入导出**：无法导入/导出术语表

### 12.2 待优化项

1. **双语术语**：支持术语的中英文映射
2. **使用统计**：记录术语在各会议中的出现次数
3. **批量操作**：支持批量确认/拒绝
4. **术语搜索**：支持模糊搜索和过滤

### 12.3 扩展方向

1. **术语推荐**：基于相似项目推荐术语
2. **术语冲突检测**：检测相似术语的冲突
3. **术语生命周期**：长期未使用的术语降级提醒
4. **跨项目共享**：支持项目间共享术语库
