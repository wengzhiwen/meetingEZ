"""
Meeting Agent CLI 入口
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel

from meeting_agent import __version__
from meeting_agent.config import Config, MEETING_META_FILE
from meeting_agent.scanner import MeetingScanner
from meeting_agent.asr.router import ASRRouter, ASRBlockedException
from meeting_agent.llm import LLMClient
from meeting_agent.memory import MemoryWriter
from meeting_agent.glossary import GlossaryManager, ContextManager, get_combined_context
from meeting_agent.models_glossary import TermType
from meeting_agent.progress import (
    clear_progress, complete_step, fail_step, init_progress, set_step,
    update_chunks, STEP_ASR, STEP_PRE_HINT, STEP_ANALYZING, STEP_MEMORY,
)

console = Console()
logger = logging.getLogger("meeting_agent")


def _fmt_ts(seconds) -> str:
    """将秒数格式化为 [HH:MM:SS]"""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "??:??:??"
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def setup_logging(verbose: bool = False):
    """配置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.basicConfig(level=level, format=fmt._fmt, datefmt=fmt.datefmt)

    # 同时写入 logs/meetingez.log（与 Web 应用共享）
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    fh = logging.FileHandler(log_dir / "meetingez.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logging.getLogger("meeting_agent").addHandler(fh)


def cmd_run(args):
    """运行 Agent"""
    config = Config()
    scanner = MeetingScanner(config)
    asr_router = ASRRouter(config)
    llm_client = LLMClient(config)
    memory_writer = MemoryWriter(config)

    # 检查配置
    if not config.is_configured:
        console.print("[red]错误: 请先配置 API Key（ZHIPU_API_KEY 和 OPENAI_API_KEY）[/red]")
        return 1

    # 确定项目目录
    if args.project and config.projects_dir:
        project_dir = config.projects_dir / args.project
        if not project_dir.exists():
            console.print(f"[red]错误: 项目不存在: {args.project}[/red]")
            return 1
    elif config.projects_dir:
        # 多项目模式但未指定项目
        projects = scanner.list_projects()
        if not projects:
            console.print("[red]错误: 没有找到项目，请先运行 init-project 创建项目[/red]")
            return 1
        console.print("[yellow]多项目模式，请指定项目 (--project)[/yellow]")
        console.print("可用项目:")
        for p in projects:
            console.print(f"  - {p.name}")
        return 1
    else:
        project_dir = None

    # 扫描会议
    console.print("[bold blue]扫描会议目录...[/bold blue]")
    tasks = scanner.scan_meetings(project_dir)

    # 如果指定了会议，过滤
    if getattr(args, 'meeting', None):
        tasks = [t for t in tasks if t.dir_name == args.meeting]
        if not tasks:
            console.print(f"[red]未找到会议: {args.meeting}[/red]")
            return 1

    if not tasks:
        console.print("[yellow]没有发现会议目录[/yellow]")
        return 0

    # 显示任务列表
    table = Table(title="会议任务")
    table.add_column("目录", style="cyan")
    table.add_column("音频", justify="center")
    table.add_column("转写", justify="center")
    table.add_column("纪要", justify="center")
    table.add_column("待处理", style="yellow")

    pending_tasks = []
    force_minutes = getattr(args, 'force_minutes', False)
    force_asr = getattr(args, 'force_asr', False)

    for task in tasks:
        asr_status = "✅" if task.has_transcript else ("🔄" if task.needs_asr else "❌")
        minutes_status = "✅" if task.has_minutes else "❌"

        needs = []
        if task.needs_asr or force_asr:
            needs.append("ASR")
        if task.needs_minutes or force_minutes:
            needs.append("纪要")

        table.add_row(
            task.dir_name,
            str(len(task.audio_files)),
            asr_status,
            minutes_status,
            ", ".join(needs) or "-",
        )

        # 强制模式下也加入待处理
        if task.needs_asr or task.needs_minutes or force_minutes or force_asr:
            pending_tasks.append(task)

    console.print(table)

    if not pending_tasks:
        console.print("[green]所有会议都已处理完成[/green]")
        return 0

    # 处理待办任务
    console.print(f"\n[bold]开始处理 {len(pending_tasks)} 个待处理会议...[/bold]\n")

    for task in pending_tasks:
        console.print(Panel(f"[bold]{task.dir_name}[/bold]", expand=False))
        init_progress(task.meeting_dir)

        # 1. ASR
        if task.needs_asr and task.audio_files:
            set_step(
                task.meeting_dir, STEP_ASR, "音频转写中",
                audio_total=len(task.audio_files),
            )
            logger.info(
                "开始 ASR: meeting=%s, audio_files=%s",
                task.dir_name, [f.name for f in task.audio_files],
            )
            with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
            ) as progress:
                progress.add_task("执行 ASR 转写...", total=None)

                try:
                    transcript = asr_router.transcribe(
                        audio_files=task.audio_files,
                        meeting_dir=task.meeting_dir,
                        force=getattr(args, 'force_asr', False),
                    )
                except ASRBlockedException as e:
                    logger.warning("ASR 被阻塞: meeting=%s, %s", task.dir_name, e)
                    fail_step(task.meeting_dir, STEP_ASR, str(e))
                    console.print(f"[red]ASR 失败，已阻塞等待重试[/red]")
                    console.print(f"[yellow]{e}[/yellow]")
                    console.print("[dim]可通过 Web GUI 立即重试或降级到智谱 ASR[/dim]")
                    continue

            if not transcript:
                logger.error("ASR 失败（无结果）: meeting=%s", task.dir_name)
                fail_step(task.meeting_dir, STEP_ASR, "无转写结果")
                console.print("[red]ASR 失败[/red]")
                continue

            console.print(f"[green]✓ ASR 完成: {len(transcript.segments)} 个片段[/green]")
            logger.info(
                "ASR 完成: meeting=%s, segments=%d, duration=%.2fs",
                task.dir_name, len(transcript.segments), transcript.duration,
            )
            complete_step(task.meeting_dir, STEP_ASR)

        # 2. 生成纪要
        # 检查转写文件是否存在（可能在本次 ASR 中刚生成）
        transcript_file = task.meeting_dir / "transcript.json"
        has_transcript_now = transcript_file.exists()

        # 判断是否需要生成纪要：原来就需要、强制生成、或本次 ASR 刚完成
        should_generate_minutes = task.needs_minutes or force_minutes or (
            task.needs_asr and has_transcript_now)

        if should_generate_minutes and has_transcript_now:

            with open(transcript_file, "r", encoding="utf-8") as f:
                transcript_data = json.load(f)

            # 构建转写文本（含说话人信息时格式化输出）
            segments = transcript_data.get("segments", [])
            has_speaker = any(seg.get("speaker") for seg in segments)
            if has_speaker:
                transcript_text = "\n".join(
                    f"[{_fmt_ts(seg.get('start', 0))}] Speaker {seg.get('speaker', '?')}: {seg['text']}"
                    for seg in segments
                )
            else:
                transcript_text = "\n".join(seg["text"] for seg in segments)

            # 加载上下文
            context_md, actions_md, recent_minutes = memory_writer.get_context_for_meeting(
                project_dir)

            # 加载术语表和人工维护的上下文 (_context.md)
            if project_dir:
                from meeting_agent.config import Settings
                glossary_config = Config(
                    Settings(
                        **{
                            k: v
                            for k, v in config.settings.model_dump().items()
                            if v is not None
                        }))
                glossary_config.settings.meetings_dir = project_dir
            else:
                glossary_config = config

            glossary_context = get_combined_context(glossary_config)

            # 加载会议元信息
            meeting_meta = scanner.load_meeting_meta(task.meeting_dir)

            # 生成会议前提示
            pre_hint = None
            if meeting_meta:
                set_step(task.meeting_dir, STEP_PRE_HINT)
                with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        console=console,
                ) as progress:
                    progress.add_task("生成会议前提示...", total=None)
                    pre_hint = llm_client.generate_pre_meeting_hint(
                        meeting_meta=meeting_meta,
                        context_md=context_md,
                        actions_md=actions_md,
                    )
                    if pre_hint:
                        memory_writer.save_pre_meeting_hint(pre_hint, task.meeting_dir)
                complete_step(task.meeting_dir, STEP_PRE_HINT)

            # 调用 GPT 分析
            set_step(task.meeting_dir, STEP_ANALYZING)
            with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
            ) as progress:
                progress.add_task("调用 GPT-5.4 分析...", total=None)

                result = llm_client.analyze_meeting(
                    transcript_text=transcript_text,
                    meeting_meta=meeting_meta,
                    project_context=context_md,
                    existing_actions=actions_md,
                    recent_minutes=recent_minutes,
                    pre_hint=pre_hint if meeting_meta else None,
                    glossary_context=glossary_context,
                    has_speaker_info=has_speaker,
                )

            if not result:
                fail_step(task.meeting_dir, STEP_ANALYZING, "GPT 分析失败")
                console.print("[red]GPT 分析失败[/red]")
                continue

            # 处理术语建议
            if result.term_suggestions:
                # 创建指向项目目录的配置
                if project_dir:
                    from meeting_agent.config import Settings
                    term_config = Config(
                        Settings(
                            **{
                                k: v
                                for k, v in config.settings.model_dump().items()
                                if v is not None
                            }))
                    term_config.settings.meetings_dir = project_dir
                else:
                    term_config = config

                glossary_mgr = GlossaryManager(term_config)
                new_terms_count = 0
                for term in result.term_suggestions:
                    try:
                        term_type = TermType(term.get("type", "other"))
                    except ValueError:
                        term_type = TermType.OTHER

                    suggestion = glossary_mgr.suggest_term(
                        canonical=term.get("canonical", ""),
                        aliases=term.get("aliases"),
                        term_type=term_type,
                        context=term.get("context"),
                        source_meeting=task.dir_name,
                    )
                    if suggestion:
                        new_terms_count += 1

                if new_terms_count > 0:
                    console.print(f"[green]✓ 发现 {new_terms_count} 个新术语建议[/green]")

            # 处理需要人工解释的问题
            if result.context_questions:
                # 使用之前创建的 glossary_config
                context_mgr = ContextManager(glossary_config)
                questions_count = context_mgr.append_questions(
                    questions=result.context_questions,
                    source_meeting=task.dir_name,
                )
                if questions_count > 0:
                    console.print(
                        f"[green]✓ 添加 {questions_count} 个待解释问题到 _context.md[/green]")

            # 保存结果
            complete_step(task.meeting_dir, STEP_ANALYZING)
            if meeting_meta:
                set_step(task.meeting_dir, STEP_MEMORY)
                memory_writer.process_analysis_result(
                    result=result,
                    meeting_meta=meeting_meta,
                    meeting_dir=task.meeting_dir,
                    project_dir=project_dir,
                )
                complete_step(task.meeting_dir, STEP_MEMORY)

            console.print(f"[green]✓ 纪要生成完成[/green]")
            clear_progress(task.meeting_dir)

        console.print()

    console.print("[bold green]所有任务处理完成！[/bold green]")
    return 0


def cmd_status(args):
    """显示项目状态"""
    config = Config()
    scanner = MeetingScanner(config)

    # 确定项目目录
    if args.project and config.projects_dir:
        project_dir = config.projects_dir / args.project
        if not project_dir.exists():
            console.print(f"[red]错误: 项目不存在: {args.project}[/red]")
            return 1
    elif config.projects_dir:
        # 多项目模式，列出所有项目
        projects = scanner.list_projects()
        if not projects:
            console.print("[yellow]没有找到项目[/yellow]")
            return 0

        console.print(Panel("[bold]项目列表[/bold]", expand=False))

        table = Table()
        table.add_column("项目名称", style="cyan")
        table.add_column("会议数", justify="right")
        table.add_column("待办数", justify="right")

        for proj in projects:
            status = scanner.get_project_status(proj)
            table.add_row(proj.name, str(status.total_meetings),
                          str(status.total_actions or "-"))

        console.print(table)
        return 0
    else:
        project_dir = None

    status = scanner.get_project_status(project_dir)

    console.print(Panel("[bold]项目状态[/bold]", expand=False))

    table = Table()
    table.add_column("指标", style="cyan")
    table.add_column("值", justify="right")

    table.add_row("会议总数", str(status.total_meetings))
    table.add_row("已处理", str(status.processed_meetings))
    table.add_row("待 ASR", str(status.pending_asr))
    table.add_row("待生成纪要", str(status.pending_minutes))

    console.print(table)

    # 待办统计
    from meeting_agent.memory import ActionsManager
    actions_mgr = ActionsManager(config)
    stats = actions_mgr.get_stats(project_dir)

    console.print("\n[bold]待办统计[/bold]")

    action_table = Table()
    action_table.add_column("状态", style="cyan")
    action_table.add_column("数量", justify="right")

    action_table.add_row("总计", str(stats["total"]))
    action_table.add_row("已完成", f"[green]{stats['completed']}[/green]")
    action_table.add_row("进行中", f"[yellow]{stats['in_progress']}[/yellow]")
    action_table.add_row("待处理", str(stats["pending"]))
    action_table.add_row("超期", f"[red]{stats['overdue']}[/red]")

    console.print(action_table)

    return 0


def cmd_actions(args):
    """管理待办事项"""
    config = Config()
    from meeting_agent.memory import ActionsManager
    actions_mgr = ActionsManager(config)

    if getattr(args, 'overdue', False):
        # 显示超期待办
        overdue = actions_mgr.get_overdue()
        if not overdue:
            console.print("[green]没有超期待办[/green]")
            return 0

        console.print("[bold red]超期待办[/bold red]\n")

        table = Table()
        table.add_column("ID", style="cyan")
        table.add_column("任务", max_width=40)
        table.add_column("负责人")
        table.add_column("截止日期")
        table.add_column("超期天数")

        from datetime import date
        today = date.today()

        for action in overdue:
            overdue_days = (today - action.due_date).days if action.due_date else 0
            table.add_row(
                action.id,
                action.task[:40],
                action.owner,
                str(action.due_date) if action.due_date else "-",
                f"[red]{overdue_days}[/red]",
            )

        console.print(table)
        return 0

    # 显示所有待办
    actions = actions_mgr.load()

    if not actions:
        console.print("[yellow]暂无待办事项[/yellow]")
        return 0

    console.print("[bold]待办事项[/bold]\n")

    table = Table()
    table.add_column("ID", style="cyan")
    table.add_column("任务", max_width=40)
    table.add_column("负责人")
    table.add_column("截止日期")
    table.add_column("状态")

    status_emoji = {
        "completed": "✅",
        "in_progress": "🔄",
        "overdue": "🔴",
        "pending": "⏳",
    }

    for action in actions:
        emoji = status_emoji.get(action.status.value, "⏳")
        table.add_row(
            action.id,
            action.task[:40],
            action.owner,
            str(action.due_date) if action.due_date else "-",
            emoji,
        )

    console.print(table)
    return 0


def cmd_complete(args):
    """标记待办完成"""
    config = Config()
    from meeting_agent.memory import ActionsManager
    actions_mgr = ActionsManager(config)

    action_id = args.action_id

    if actions_mgr.mark_completed(action_id):
        console.print(f"[green]✓ 待办 {action_id} 已标记为完成[/green]")
        return 0
    else:
        console.print(f"[red]✗ 未找到待办 {action_id}[/red]")
        return 1


def cmd_init_project(args):
    """初始化项目"""
    from datetime import datetime

    config = Config()

    from meeting_agent.memory import MemoryWriter
    memory_writer = MemoryWriter(config)

    # 判断多项目模式还是单项目模式
    if config.projects_dir:
        # 多项目模式
        if not args.name:
            console.print("[red]错误: 多项目模式需要指定项目名称 (--name)[/red]")
            return 1

        project_dir = config.projects_dir / args.name
        project_dir.mkdir(parents=True, exist_ok=True)

        memory_writer.initialize_project(
            project_name=args.name,
            description=args.description or "",
            team=args.team.split(",") if args.team else None,
            start_date=args.start_date,
            project_dir=project_dir,
        )

        console.print(f"[green]✓ 项目初始化完成[/green]")
        console.print(f"  项目名称: {args.name}")
        console.print(f"  项目目录: {project_dir}")
    else:
        # 单项目模式
        config.ensure_dirs()

        memory_writer.initialize_project(
            project_name=args.name or "我的项目",
            description=args.description or "",
            team=args.team.split(",") if args.team else None,
            start_date=args.start_date,
        )

        console.print(f"[green]✓ 项目初始化完成[/green]")
        console.print(f"  目录: {config.meetings_dir}")

    return 0


def cmd_init_meeting(args):
    """初始化会议目录"""
    import re
    from datetime import datetime, date as date_type

    config = Config()

    # 确定项目目录
    if args.project and config.projects_dir:
        project_dir = config.projects_dir / args.project
    elif config.projects_dir:
        console.print("[red]错误: 多项目模式需要指定项目 (--project)[/red]")
        return 1
    else:
        project_dir = config.meetings_dir

    meeting_dir = project_dir / args.name
    meeting_dir.mkdir(parents=True, exist_ok=True)

    # 创建 _meeting.json
    from meeting_agent.models import LanguageMode, MeetingMeta, MeetingType

    meeting_date = None
    if args.date:
        meeting_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        # 从目录名解析日期
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", args.name)
        if match:
            meeting_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        else:
            meeting_date = date_type.today()

    # 解析会议类型
    try:
        meeting_type = MeetingType(args.type)
    except ValueError:
        meeting_type = MeetingType.OTHER

    try:
        language_mode = LanguageMode(args.language_mode)
    except ValueError:
        language_mode = LanguageMode.SINGLE_PRIMARY

    meta = MeetingMeta(
        date=meeting_date,
        title=args.name.split("_", 1)[-1] if "_" in args.name else args.name,
        type=meeting_type,
        participants=args.participants.split(",") if args.participants else [],
        notes=args.notes,
        language_mode=language_mode,
        primary_language=args.primary_language,
        secondary_language=args.secondary_language,
    )

    meta_file = meeting_dir / MEETING_META_FILE
    with open(meta_file, "w", encoding="utf-8") as f:
        import json
        json.dump(meta.model_dump(), f, ensure_ascii=False, indent=2, default=str)

    console.print(f"[green]✓ 会议目录创建完成[/green]")
    console.print(f"  目录: {meeting_dir}")
    console.print(f"  元信息: {meta_file}")
    console.print(f"  语言画像: {meta.language_profile_label()}")
    run_hint = "python -m meeting_agent run"
    if args.project:
        run_hint += f" --project {args.project}"
    console.print(f"\n请将录音文件放入该目录后运行: {run_hint}")
    return 0


def cmd_terms(args):
    """管理术语表"""
    config = Config()

    # 确定项目目录
    if args.project and config.projects_dir:
        from meeting_agent.config import Settings
        # 创建新的配置，指向项目目录
        project_dir = config.projects_dir / args.project
        new_settings = Settings(**{
            k: v
            for k, v in config.settings.model_dump().items() if v is not None
        })
        new_settings.meetings_dir = project_dir
        config = Config(new_settings)

    from meeting_agent.glossary import GlossaryManager, ContextManager
    from meeting_agent.models_glossary import TermType

    glossary_mgr = GlossaryManager(config)
    context_mgr = ContextManager(config)

    # 初始化上下文文件
    if getattr(args, 'init_context', False):
        context_mgr.initialize()
        console.print(f"[green]✓ 已初始化上下文文件: {context_mgr.context_file}[/green]")
        return 0

    # 确认术语
    if args.confirm:
        if glossary_mgr.approve_suggestion(args.confirm):
            console.print(f"[green]✓ 已确认术语: {args.confirm}[/green]")
        else:
            console.print(f"[yellow]未找到待审核术语: {args.confirm}[/yellow]")
        return 0

    # 拒绝术语
    if args.reject:
        if glossary_mgr.reject_suggestion(args.reject, args.reason):
            console.print(f"[green]✓ 已拒绝术语: {args.reject}[/green]")
        else:
            console.print(f"[yellow]未找到待审核术语: {args.reject}[/yellow]")
        return 0

    # 手动添加术语
    if args.add:
        aliases = args.aliases.split(",") if args.aliases else []
        try:
            term_type = TermType(args.type)
        except ValueError:
            term_type = TermType.OTHER

        entry = glossary_mgr.add_term(
            canonical=args.add,
            aliases=aliases,
            type=term_type,
            auto_generated=False,
        )
        glossary_mgr.confirm_term(args.add)
        console.print(f"[green]✓ 已添加术语: {args.add}[/green]")
        if aliases:
            console.print(f"  别名: {', '.join(aliases)}")
        return 0

    # 显示术语表
    console.print(Panel("[bold]术语表[/bold]", expand=False))

    # 已确认的术语
    glossary = glossary_mgr.load_glossary()
    confirmed = [e for e in glossary.entries if e.confirmed_at]

    if confirmed:
        console.print(f"\n[bold]已确认术语 ({len(confirmed)})[/bold]\n")
        table = Table()
        table.add_column("标准名称", style="cyan")
        table.add_column("类型")
        table.add_column("别名")

        type_names = {
            TermType.PERSON: "人名",
            TermType.PRODUCT: "产品",
            TermType.TECHNICAL: "技术",
            TermType.PROJECT: "项目",
            TermType.ABBREVIATION: "缩写",
            TermType.OTHER: "其他",
        }

        for entry in sorted(confirmed, key=lambda x: x.canonical):
            table.add_row(entry.canonical, type_names.get(entry.type, "其他"),
                          ", ".join(entry.aliases) if entry.aliases else "-")
        console.print(table)

    # 待审核术语
    if args.pending or not confirmed:
        pending = glossary_mgr.load_pending()

        if pending.suggestions:
            console.print(f"\n[yellow]待审核术语 ({len(pending.suggestions)})[/yellow]\n")

            pending_table = Table()
            pending_table.add_column("标准名称", style="yellow")
            pending_table.add_column("类型")
            pending_table.add_column("别名")
            pending_table.add_column("频率")

            type_names = {
                TermType.PERSON: "人名",
                TermType.PRODUCT: "产品",
                TermType.TECHNICAL: "技术",
                TermType.PROJECT: "项目",
                TermType.ABBREVIATION: "缩写",
                TermType.OTHER: "其他",
            }

            for suggestion in sorted(pending.suggestions, key=lambda x: -x.frequency):
                pending_table.add_row(
                    suggestion.canonical, type_names.get(suggestion.type, "其他"),
                    ", ".join(suggestion.aliases) if suggestion.aliases else "-",
                    str(suggestion.frequency))
            console.print(pending_table)

            console.print(
                f"\n[dim]确认: python -m meeting_agent terms --confirm \"术语\"[/dim]")
            console.print(
                f"[dim]拒绝: python -m meeting_agent terms --reject \"术语\" --reason \"原因\"[/dim]"
            )

    # 显示拒绝的术语数量
    rejected = glossary_mgr.load_rejected()
    if rejected.rejected:
        console.print(f"\n[dim]已拒绝术语: {len(rejected.rejected)} 个[/dim]")

    return 0


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        prog="meeting_agent",
        description="会议纪要整理 Agent - 自动化会议转写与纪要生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    parser.add_argument("--version",
                        action="version",
                        version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # run 命令
    run_parser = subparsers.add_parser("run", help="运行 Agent 处理会议")
    run_parser.add_argument("--project", type=str, help="项目名称（多项目模式必需）")
    run_parser.add_argument("--watch", action="store_true", help="持续监控模式")
    run_parser.add_argument("--interval", type=int, default=300, help="监控间隔（秒）")
    run_parser.add_argument("--meeting", type=str, help="指定会议目录名")
    run_parser.add_argument("--asr-only", action="store_true", help="仅执行 ASR")
    run_parser.add_argument("--minutes-only", action="store_true", help="仅生成纪要")
    run_parser.add_argument("--force", action="store_true", help="强制重新处理")
    run_parser.add_argument("--force-asr", action="store_true", help="强制重新 ASR")
    run_parser.add_argument("--force-minutes", action="store_true", help="强制重新生成纪要")
    run_parser.set_defaults(func=cmd_run)

    # status 命令
    status_parser = subparsers.add_parser("status", help="显示项目状态")
    status_parser.add_argument("--project", type=str, help="项目名称（多项目模式）")
    status_parser.set_defaults(func=cmd_status)

    # actions 命令
    actions_parser = subparsers.add_parser("actions", help="管理待办事项")
    actions_parser.add_argument("--overdue", action="store_true", help="仅显示超期待办")
    actions_parser.set_defaults(func=cmd_actions)

    # complete 命令
    complete_parser = subparsers.add_parser("complete", help="标记待办完成")
    complete_parser.add_argument("action_id", help="待办 ID（如 A001）")
    complete_parser.set_defaults(func=cmd_complete)

    # init-project 命令
    init_project_parser = subparsers.add_parser("init-project", help="初始化项目")
    init_project_parser.add_argument("--name", type=str, help="项目名称")
    init_project_parser.add_argument("--description", type=str, help="项目描述")
    init_project_parser.add_argument("--team", type=str, help="团队成员（逗号分隔）")
    init_project_parser.add_argument("--start-date", type=str, help="启动日期")
    init_project_parser.set_defaults(func=cmd_init_project)

    # init-meeting 命令
    init_meeting_parser = subparsers.add_parser("init-meeting", help="初始化会议目录")
    init_meeting_parser.add_argument("name", help="会议目录名（如 2025-03-18_产品评审会）")
    init_meeting_parser.add_argument("--project", type=str, help="项目名称（多项目模式必需）")
    init_meeting_parser.add_argument("--date", type=str, help="会议日期（YYYY-MM-DD）")
    init_meeting_parser.add_argument("--type", type=str, default="other", help="会议类型")
    init_meeting_parser.add_argument("--participants", type=str, help="参会人员（逗号分隔）")
    init_meeting_parser.add_argument("--notes", type=str, help="特别说明")
    init_meeting_parser.add_argument(
        "--language-mode",
        type=str,
        default="single_primary",
        help="语言模式：single_primary 或 bilingual",
    )
    init_meeting_parser.add_argument(
        "--primary-language",
        type=str,
        default="zh-CN",
        help="主要语言（如 zh-CN / en / ja）",
    )
    init_meeting_parser.add_argument(
        "--secondary-language",
        type=str,
        help="第二语言（双语言会议时使用）",
    )
    init_meeting_parser.set_defaults(func=cmd_init_meeting)

    # terms 命令（术语表管理）
    terms_parser = subparsers.add_parser("terms", help="管理术语表")
    terms_parser.add_argument("--project", type=str, help="项目名称（多项目模式）")
    terms_parser.add_argument("--pending", action="store_true", help="仅显示待审核术语")
    terms_parser.add_argument("--confirm", type=str, metavar="TERM", help="确认术语")
    terms_parser.add_argument("--reject", type=str, metavar="TERM", help="拒绝术语")
    terms_parser.add_argument("--reason", type=str, help="拒绝原因")
    terms_parser.add_argument("--add", type=str, metavar="TERM", help="手动添加术语")
    terms_parser.add_argument("--aliases", type=str, help="术语别名（逗号分隔）")
    terms_parser.add_argument("--type", type=str, default="other", help="术语类型")
    terms_parser.add_argument("--init-context", action="store_true", help="初始化上下文文件")
    terms_parser.set_defaults(func=cmd_terms)

    args = parser.parse_args()

    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
