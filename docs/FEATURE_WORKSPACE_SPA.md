# Web 工作台功能详细设计

## 1. 功能概述

### 1.1 产品定位

Web 工作台是 MeetingEZ 的主入口界面，为命令行 Agent 提供可视化操作界面。采用 SPA（单页应用）架构，通过前端路由实现无刷新页面切换。

### 1.2 核心价值

- **项目管理**：创建、查看、编辑项目
- **会议管理**：创建会议、上传音频、触发处理、查看结果
- **术语管理**：术语审核、添加、编辑、删除
- **背景说明**：维护项目背景知识
- **快速入口**：快速模式转写入口

### 1.3 目标用户

- 需要管理多个会议项目的团队
- 需要审核术语的项目管理员
- 需要查看会议纪要和转写结果的用户

---

## 2. 功能边界

### 2.1 职责范围

| 功能 | 是否负责 | 说明 |
|------|----------|------|
| 项目 CRUD | ✅ | 创建、查看、编辑项目 |
| 会议管理 | ✅ | 创建会议、上传音频、触发处理 |
| 术语管理 | ✅ | 审核术语、手动添加 |
| 背景说明 | ✅ | 编辑项目背景知识 |
| 文件查看/编辑 | ✅ | 纪要、转写、上下文文件 |
| 实时转写 | ❌ | 由实时页 `/realtime` 负责 |
| ASR 处理 | ❌ | 由后端 Agent 负责 |
| 纪要生成 | ❌ | 由后端 Agent 负责 |

### 2.2 与其他功能的关系

```
┌─────────────────────────────────────────────────────────────┐
│                      Web 工作台                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌─────────────────────────────────────────────────────┐   │
│   │                    前端 SPA                          │   │
│   │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │   │
│   │  │ Dashboard│ │ 项目详情 │ │ 术语管理 │ │ 背景说明 │   │   │
│   │  └─────────┘ └─────────┘ └─────────┘ └─────────┘   │   │
│   │  ┌─────────┐ ┌─────────┐                           │   │
│   │  │ 会议列表 │ │ 文件编辑 │                           │   │
│   │  └─────────┘ └─────────┘                           │   │
│   └─────────────────────────────────────────────────────┘   │
│                           │                                 │
│                           ▼                                 │
│   ┌─────────────────────────────────────────────────────┐   │
│   │                 JSON API 层                          │   │
│   │  /api/workspace/dashboard                           │   │
│   │  /api/workspace/project/:id                         │   │
│   │  /api/workspace/project/:id/glossary                │   │
│   │  /api/workspace/project/:id/meeting/:dir/process    │   │
│   └─────────────────────────────────────────────────────┘   │
│                           │                                 │
│                           ▼                                 │
│   ┌─────────────────────────────────────────────────────┐   │
│   │                 后端服务层                           │   │
│   │  workspace_service.py (数据聚合)                    │   │
│   │  meeting_agent (ASR + 纪要)                         │   │
│   └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 架构设计

### 3.1 SPA 架构

```
┌─────────────────────────────────────────────────────────────┐
│                     workspace_spa.html                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌─────────────┐  ┌─────────────────────────────────────┐  │
│   │  Sidebar    │  │  Main Content                       │  │
│   │  ─────────  │  │  ┌─────────────────────────────┐   │  │
│   │  项目列表   │  │  │  Topbar (面包屑)            │   │  │
│   │  + 新建项目 │  │  └─────────────────────────────┘   │  │
│   │             │  │  ┌─────────────────────────────┐   │  │
│   │             │  │  │  Tab Bar (项目视图)         │   │  │
│   │             │  │  └─────────────────────────────┘   │  │
│   │             │  │  ┌─────────────────────────────┐   │  │
│   │             │  │  │  Tab Content               │   │  │
│   │             │  │  │  - Overview                │   │  │
│   │             │  │  │  - Meetings                │   │  │
│   │             │  │  │  - Glossary                │   │  │
│   │             │  │  │  - Background              │   │  │
│   │             │  │  └─────────────────────────────┘   │  │
│   └─────────────┘  └─────────────────────────────────────┘  │
│                                                             │
│   ┌─────────────────────────────────────────────────────┐   │
│   │  Modal (弹窗)                                       │   │
│   │  SlideOver (右侧滑出面板)                          │   │
│   │  Toast (通知)                                       │   │
│   └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 前端模块结构

```
app/static/js/workspace/
├── main.js           # 入口，路由注册，事件绑定
├── router.js         # Hash 路由解析
├── state.js          # 简单状态管理
├── api.js            # API 封装层
├── toast.js          # Toast 通知
├── components/
│   ├── sidebar.js    # 侧边栏
│   ├── topbar.js     # 顶部面包屑
│   ├── dashboard.js  # 仪表盘视图
│   ├── project-tabs.js  # 项目 Tab 容器
│   ├── overview.js   # 概览 Tab
│   ├── meetings.js   # 会议列表 Tab
│   ├── glossary.js   # 术语管理 Tab
│   ├── background.js # 背景说明 Tab
│   ├── modal.js      # 模态弹窗
│   └── slide-over.js # 右侧滑出面板
```

### 3.3 路由设计

| Hash 路由 | 视图 | 说明 |
|-----------|------|------|
| `#` 或 `#dashboard` | Dashboard | 仪表盘 |
| `#project/{id}` | Project Overview | 项目概览 |
| `#project/{id}/meetings` | Meetings | 会议列表 |
| `#project/{id}/glossary` | Glossary | 术语管理 |
| `#project/{id}/background` | Background | 背景说明 |

```javascript
// 路由解析
function parseHash() {
  const raw = (location.hash || '#').slice(1);
  if (!raw || raw === 'dashboard') {
    return { view: 'dashboard' };
  }

  // #project/{id}/meetings  #project/{id}/glossary
  const m = raw.match(/^project\/([^/]+)(?:\/(.+))?$/);
  if (m) {
    return {
      view: 'project',
      projectId: decodeURIComponent(m[1]),
      tab: m[2] || 'overview'
    };
  }

  return { view: 'dashboard' };
}
```

---

## 4. 数据结构

### 4.1 全局状态

```javascript
// state.js
const state = {
  projects: [],           // 项目列表
  currentProjectId: null, // 当前项目 ID
  currentTab: 'overview', // 当前 Tab
};

// 订阅模式
const listeners = new Set();

function setState(patch) {
  Object.assign(state, patch);
  listeners.forEach(fn => fn(state));
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
```

### 4.2 Dashboard 数据

```javascript
// GET /api/workspace/dashboard 返回
{
  projects: [
    {
      id: "project_abc",
      name: "MeetingEZ 开发",
      description: "智能会议纪要系统",
      meeting_count: 12,
      pending_asr: 2,
      pending_minutes: 1,
      glossary_confirmed: 15,
      is_default: false
    }
  ],
  workspace_summary: {
    meeting_count: 45,
    pending_count: 3
  },
  can_create_project: true
}
```

### 4.3 项目详情数据

```javascript
// GET /api/workspace/project/:id 返回
{
  project: {
    id: "project_abc",
    name: "MeetingEZ 开发",
    description: "智能会议纪要系统",
    team: ["张三", "李四"],
    start_date: "2026-01-01",
    meeting_count: 12,
    pending_asr: 2,
    pending_minutes: 1,
    glossary_confirmed: 15,
    glossary_pending: 3,
    actions_total: 20,
    actions_overdue: 1,
    background_exists: true
  },
  meetings: [
    {
      dir_name: "2026-03-25_需求评审",
      title: "需求评审",
      date: "2026-03-25",
      type: "review",
      primary_language: "zh",
      secondary_language: "",
      language_mode: "single_primary",
      language_profile: "中文",
      notes: "重点关注登录模块",
      has_audio: true,
      has_transcript: true,
      has_minutes: true,
      needs_asr: false,
      needs_minutes: false,
      is_processing: false,
      pending_label: "",
      audio_files: [
        { name: "recording.m4a", size: 1024000, size_label: "1.0 MB" }
      ],
      files: [
        { name: "minutes.md", label: "会议纪要", size: 5000, size_label: "5 KB", updated_at: "2026-03-25 16:30" }
      ]
    }
  ],
  recent_actions: [
    { id: "A001", task: "完成文档", owner: "张三", status: "in_progress" }
  ],
  meeting_type_options: [
    ["review", "评审会"],
    ["weekly", "周会"],
    // ...
  ],
  language_options: [
    ["zh", "中文 (简体)"],
    ["en", "English"],
    // ...
  ]
}
```

### 4.4 术语数据

```javascript
// GET /api/workspace/project/:id/glossary 返回
{
  project: { id: "...", name: "..." },
  terms: [
    {
      state: "confirmed",        // confirmed | pending | rejected
      canonical: "MeetingEZ",
      aliases: ["米听易", "meeting-ez"],
      type: "product",           // person | technical | product | project | abbr | other
      context: "产品名称",
      source_meeting: "2026-03-20 启动会",
      frequency: 5,              // 待审核术语的出现频次
      reason: ""                 // 拒绝原因（仅 rejected）
    }
  ]
}
```

---

## 5. 详细设计

### 5.1 仪表盘（Dashboard）

#### 5.1.1 页面结构

```
┌─────────────────────────────────────────────────────────────┐
│  工作台                                                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐                │
│   │    3     │  │    45    │  │     3    │                │
│   │   项目   │  │   会议   │  │  待处理  │                │
│   └──────────┘  └──────────┘  └──────────┘                │
│                                                             │
│   全部项目                                                  │
│   ┌─────────────────────────────────────────────────────┐   │
│   │  MeetingEZ 开发                    12 会议 · 2 待处理│   │
│   │  智能会议纪要系统                        15 术语      │   │
│   └─────────────────────────────────────────────────────┘   │
│   ┌─────────────────────────────────────────────────────┐   │
│   │  客户 A 项目                         8 会议 · 1 待处理│   │
│   │  客户需求讨论与跟进                                  │   │
│   └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 5.1.2 功能说明

- **统计卡片**：显示项目数、会议数、待处理数
- **项目列表**：按卡片形式展示所有项目
- **快速入口**：点击项目卡片进入项目详情
- **空状态**：无项目时显示"启动快速转写"入口

### 5.2 项目详情页

#### 5.2.1 页面结构

```
┌─────────────────────────────────────────────────────────────┐
│  工作台 > MeetingEZ 开发                                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌───────────────────────────────────────────────────────┐ │
│   │ [概览] [会议] [术语] [背景]                           │ │
│   └───────────────────────────────────────────────────────┘ │
│                                                             │
│   ┌───────────────────────────────────────────────────────┐ │
│   │  Tab Content                                          │ │
│   │                                                       │ │
│   └───────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 5.2.2 Tab 列表

| Tab | 功能 |
|-----|------|
| 概览 | 项目信息、统计、近期行动项、新建会议 |
| 会议 | 会议列表、音频管理、处理触发、文件查看 |
| 术语 | 术语审核、添加、编辑、删除 |
| 背景 | 项目背景说明管理 |

### 5.3 会议列表

#### 5.3.1 页面结构

```
┌─────────────────────────────────────────────────────────────┐
│  ▶ 需求评审                              2026-03-25  评审会 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  会议配置                                           │   │
│  │  标题: 需求评审    日期: 2026-03-25    类型: 评审会 │   │
│  │  语言: 中文        备注: 重点关注登录模块           │   │
│  │                                          [编辑]     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  文件                                               │   │
│  │  会议纪要     5 KB · 2026-03-25 16:30              │   │
│  │  正式转写     20 KB · 2026-03-25 16:00             │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  音频文件 (1)                          已转写       │   │
│  │  recording.m4a    1.0 MB                            │   │
│  │  ▶ [audio player]                      [删除]      │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

#### 5.3.2 会议状态

| 状态 | 条件 | 显示 |
|------|------|------|
| 处理中 | `_processing.lock` 存在 | "处理中..." |
| 待转写 | 有音频，无转写 | "待转写" |
| 待纪要 | 有转写，无纪要 | "待纪要" |
| 已完成 | 有纪要 | 无状态标签 |

#### 5.3.3 处理操作

| 操作 | 说明 | API |
|------|------|-----|
| 完整处理 | ASR + 纪要 + 记忆更新 | `POST .../process {action: "full"}` |
| 仅处理纪要 | 跳过 ASR，仅生成纪要 | `POST .../process {action: "minutes"}` |
| 重新处理 | 强制重新生成 | `POST .../process {action: "reprocess"}` |

#### 5.3.4 处理状态轮询

```javascript
// 启动轮询
function _startPolling(projectId, dir) {
  const timerId = setInterval(async () => {
    const result = await api.getMeetingProcessStatus(projectId, dir);
    if (!result.is_processing) {
      clearInterval(timerId);
      if (result.error) {
        showToast('处理失败：' + result.error, 'error');
      } else {
        showToast('处理完成', 'success');
      }
      // 刷新页面数据
      await render(projectId, 'meetings');
    }
  }, 3000);
}
```

### 5.4 术语管理

#### 5.4.1 页面结构

```
┌─────────────────────────────────────────────────────────────┐
│  术语列表                    12 已确认 · 3 待审核 · 1 已拒绝 │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ [全部] [已确认] [待审核] [已拒绝]                       │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                             │
│  [+ 手动添加术语]                                          │
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  MeetingEZ                     [已确认] [产品]          │ │
│  │  别名: 米听易, meeting-ez                               │ │
│  │  产品名称                              [编辑] [↩ 待审核]│ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  API Key                      [待审核] [技术] · 5 次    │ │
│  │  来源: 2026-03-20 启动会                 [确认] [拒绝] │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

#### 5.4.2 术语状态流转

```
                    ┌─────────────┐
                    │   pending   │  ← 新增术语（Agent 提取或手动添加）
                    │   待审核    │
                    └─────────────┘
                     /           \
           [确认]   /             \   [拒绝]
                   ▼               ▼
          ┌─────────────┐   ┌─────────────┐
          │  confirmed  │   │  rejected   │
          │   已确认    │   │   已拒绝    │
          └─────────────┘   └─────────────┘
               │                   │
               │ [回退]            │ [回退]
               └───────┬───────────┘
                       ▼
               ┌─────────────┐
               │   pending   │
               │   待审核    │
               └─────────────┘
```

#### 5.4.3 术语类型

| 类型 | 说明 |
|------|------|
| `person` | 人名 |
| `technical` | 技术术语 |
| `product` | 产品名 |
| `project` | 项目名 |
| `abbr` | 缩写 |
| `other` | 其他 |

### 5.5 背景说明

#### 5.5.1 页面结构

```
┌─────────────────────────────────────────────────────────────┐
│  背景说明                                                   │
│  [+ 添加条目]                                              │
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  什么是 MeetingEZ？                                     │ │
│  │  Q: 这个系统是做什么的？                                │ │
│  │  A: 智能会议纪要系统，自动生成会议纪要...               │ │
│  │  来源: 2026-03-20 启动会              [编辑] [删除]    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  数据存储位置                                           │ │
│  │  Q: 会议录音存在哪里？                                  │ │
│  │  A: 本地文件系统，按项目/会议目录组织...                │ │
│  │                                        [编辑] [删除]    │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

#### 5.5.2 条目结构

```javascript
{
  id: "entry_001",
  topic: "什么是 MeetingEZ？",
  question: "这个系统是做什么的？",
  answer: "智能会议纪要系统，自动生成会议纪要...",
  source_meeting: "2026-03-20 启动会",
  created_at: "2026-03-20T10:00:00",
  updated_at: "2026-03-20T10:00:00"
}
```

### 5.6 文件查看/编辑（SlideOver）

#### 5.6.1 触发方式

- 点击会议列表中的文件名
- 点击"查看转写"按钮

#### 5.6.2 功能说明

| 文件类型 | 可编辑 | 说明 |
|----------|--------|------|
| `.md` | ✅ | Markdown 文件，如纪要 |
| `.json` | ✅ | JSON 文件，如转写结果 |
| `.txt` | ✅ | 纯文本文件 |
| `.csv` | ✅ | CSV 文件 |
| `.log` | ✅ | 日志文件 |
| 其他 | ❌ | 只读显示 |

### 5.7 新建项目

#### 5.7.1 表单字段

| 字段 | 必填 | 说明 |
|------|------|------|
| 项目名称 | ✅ | 项目显示名 |
| 描述 | ❌ | 项目简介 |
| 团队成员 | ❌ | 逗号分隔 |
| 开始日期 | ❌ | 默认今天 |

#### 5.7.2 创建流程

```
用户点击 "+" 按钮
    │
    ▼
弹出模态框，填写表单
    │
    ▼
POST /api/workspace/project/create
    │
    ▼
创建项目目录
    │
    ├─▶ 写入 _project.json
    ├─▶ 初始化 context.md
    ├─▶ 初始化 timeline.md
    ├─▶ 初始化 actions.md
    └─▶ 初始化 _context.md
    │
    ▼
刷新侧边栏，跳转到项目详情页
```

---

## 6. 与 Agent 的协作

### 6.1 会议处理流程

```
Web 工作台                          后端 Agent
    │                                   │
    │  1. 用户点击"完整处理"           │
    │  ───────────────────────────────▶│
    │                                   │
    │  2. POST /api/.../process        │
    │     {action: "full"}             │
    │  ───────────────────────────────▶│
    │                                   │
    │  3. 创建锁文件 _processing.lock  │
    │  ◀───────────────────────────────│
    │     返回 {ok: true}              │
    │                                   │
    │  4. 后台线程启动处理              │
    │     subprocess: meeting_agent    │
    │                         ─────────▶│
    │                                   │
    │  5. 轮询处理状态                  │
    │     GET .../process/status       │
    │  ───────────────────────────────▶│
    │                                   │
    │  6. 返回 {is_processing: true}   │
    │  ◀───────────────────────────────│
    │                                   │
    │  ... 每 3 秒轮询 ...              │
    │                                   │
    │  7. Agent 完成处理                │
    │     删除锁文件                    │
    │                         ◀─────────│
    │                                   │
    │  8. 返回 {is_processing: false}  │
    │  ◀───────────────────────────────│
    │                                   │
    │  9. 刷新页面数据                  │
    │                                   │
```

### 6.2 状态检测机制

```python
# 锁文件检测
lock_file = meeting_dir / "_processing.lock"
is_processing = lock_file.exists()

# 错误文件检测
error_file = meeting_dir / "_processing.error"
if error_file.exists():
    error_msg = error_file.read_text()
```

### 6.3 处理日志

```python
# 日志文件
log_file = meeting_dir / "_processing.log"

# Agent 运行日志写入
result = subprocess.run(cmd, capture_output=True, text=True)
log_file.write_text(result.stdout + result.stderr)
```

---

## 7. API 接口（功能视角）

### 7.1 仪表盘

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/workspace/dashboard` | GET | 获取仪表盘数据 |

### 7.2 项目管理

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/workspace/project/create` | POST | 创建项目 |
| `/api/workspace/project/:id` | GET | 获取项目详情 |
| `/api/workspace/project/:id` | PUT | 更新项目信息 |

### 7.3 会议管理

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/workspace/project/:id/meeting/create` | POST | 创建会议 |
| `/api/workspace/project/:id/meeting/:dir` | PUT | 更新会议信息 |
| `/api/workspace/project/:id/meeting/:dir/audio/upload` | POST | 上传音频 |
| `/api/workspace/project/:id/meeting/:dir/process` | POST | 触发处理 |
| `/api/workspace/project/:id/meeting/:dir/process/status` | GET | 查询处理状态 |

### 7.4 术语管理

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/workspace/project/:id/glossary` | GET | 获取术语列表 |
| `/api/workspace/project/:id/glossary/entries` | POST | 添加术语 |
| `/api/workspace/project/:id/glossary/entries/:canonical` | PUT | 更新术语 |
| `/api/workspace/project/:id/glossary/entries/:canonical` | DELETE | 删除术语 |
| `/api/workspace/project/:id/glossary/approve` | POST | 确认术语 |
| `/api/workspace/project/:id/glossary/reject` | POST | 拒绝术语 |
| `/api/workspace/project/:id/glossary/revert` | POST | 回退术语 |

### 7.5 背景说明

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/workspace/project/:id/background` | GET | 获取背景说明 |
| `/api/workspace/project/:id/background/entries` | POST | 添加条目 |
| `/api/workspace/project/:id/background/entries/:id` | PUT | 更新条目 |
| `/api/workspace/project/:id/background/entries/:id` | DELETE | 删除条目 |

---

## 8. 错误处理

### 8.1 常见错误

| 错误类型 | 原因 | 处理方式 |
|----------|------|----------|
| 项目不存在 | ID 无效或目录被删除 | Toast 提示，返回仪表盘 |
| 会议不存在 | 目录名无效 | Toast 提示 |
| 上传失败 | 文件格式不支持/网络错误 | 模态框显示错误 |
| 处理失败 | Agent 执行错误 | 显示 `_processing.error` 内容 |

### 8.2 Toast 通知

```javascript
// 成功
showToast('项目已创建', 'success');

// 错误
showToast('加载失败: ' + err.message, 'error');
```

---

## 9. 性能考量

### 9.1 缓存策略

```javascript
// 项目数据缓存（切换 Tab 时复用）
let cachedProjectData = null;
let cachedProjectId = null;

export async function render(projectId, tab) {
  // 仅在切换项目时重新加载
  if (cachedProjectId !== projectId) {
    cachedProjectData = await api.getProject(projectId);
    cachedProjectId = projectId;
  }
  // ...
}

// 手动失效缓存
export function invalidateCache(projectId) {
  if (cachedProjectId === projectId) {
    cachedProjectData = null;
    cachedProjectId = null;
  }
}
```

### 9.2 轮询控制

```javascript
// 避免重复轮询
const _pollingTimers = new Map(); // dir -> timerId

function _startPolling(projectId, dir) {
  if (_pollingTimers.has(dir)) return;  // 已在轮询
  // ...
}

// 组件销毁时应清理定时器
```

---

## 10. 未来规划

### 10.1 已知限制

1. **无实时更新**：需要手动刷新或轮询
2. **无批量操作**：无法批量处理多个会议
3. **无拖拽上传**：音频上传需要点击选择
4. **无协作功能**：多人同时编辑可能冲突

### 10.2 待优化项

1. **WebSocket 推送**：处理状态实时推送
2. **批量处理**：选择多个会议批量触发
3. **拖拽上传**：支持拖拽音频文件
4. **离线支持**：Service Worker 缓存

### 10.3 扩展方向

1. **项目权限**：多人协作权限控制
2. **评论功能**：对纪要、术语进行评论
3. **导出功能**：导出项目报告
4. **移动端适配**：响应式布局优化
