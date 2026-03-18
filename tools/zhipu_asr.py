#!/usr/bin/env python3
"""
智谱AI 云端 ASR 转录工具

独立工具，支持 m4a、mp3、wav 等格式的音频转录。
使用 GLM-ASR-2512 模型，长音频自动分块处理。

用法:
    python zhipu_asr.py input.m4a
    python zhipu_asr.py input.mp3 -o result.txt -f json
    python zhipu_asr.py input.wav --api-key YOUR_API_KEY

环境变量:
    ZHIPU_API_KEY: 智谱AI API Key (必需)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("zhipu_asr")


def get_audio_duration(audio_path: Path) -> float:
    """使用 ffprobe 获取音频时长"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception as e:
        logger.warning("获取音频时长失败: %s", e)
    return 0.0


def split_audio_chunk(
    audio_path: Path,
    output_path: Path,
    start_time: float,
    duration: float,
) -> bool:
    """使用 ffmpeg 切割音频片段为 mp3 格式"""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_time),
        "-i", str(audio_path),
        "-t", str(duration),
        "-ar", "16000",
        "-ac", "1",
        "-f", "mp3",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.returncode == 0
    except Exception as e:
        logger.error("切割音频失败: %s", e)
        return False


def transcribe_chunk(
    chunk_path: Path,
    model: str,
    api_key: str,
    base_url: str,
) -> dict:
    """调用智谱AI API 转录单个音频块"""
    import requests

    endpoint = f"{base_url.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    with open(chunk_path, "rb") as f:
        files = {"file": (chunk_path.name, f)}
        data = {"model": model, "stream": "false"}
        response = requests.post(
            endpoint,
            headers=headers,
            files=files,
            data=data,
            timeout=120,
        )
    response.raise_for_status()
    return response.json()


def merge_chunk_results(
    chunk_results: List[dict],
    chunk_start_times: List[float],
    overlap_seconds: float = 2.0,
) -> List[dict]:
    """
    合并多个块的转录结果，处理重叠部分

    重叠区域的处理策略：
    - 每个块的开头 overlap_seconds 秒是前一块的尾部重复
    - 对于重叠区域，保留时间戳更准确的结果（即该片段所在块的结果）
    - 如果 API 只返回 text 没有 segments，则不做重叠跳过（无法精确定位）
    """
    if not chunk_results:
        return []

    all_segments = []

    for idx, (result, start_time) in enumerate(zip(chunk_results, chunk_start_times)):
        if not isinstance(result, dict):
            continue

        segments = []
        if "segments" in result and isinstance(result["segments"], list):
            for seg in result["segments"]:
                text = str(seg.get("text", "")).strip()
                if not text:
                    continue
                # 转换为全局时间戳
                local_start = float(seg.get("start", 0))
                local_end = float(seg.get("end", 0))
                segments.append({
                    "global_start": start_time + local_start,
                    "global_end": start_time + local_end,
                    "local_start": local_start,
                    "local_end": local_end,
                    "chunk_idx": idx,
                    "has_precise_time": True,  # 有精确时间戳
                    "text": text,
                })
        elif "text" in result:
            text = str(result["text"]).strip()
            if text:
                # 没有时间戳，使用块的开始时间
                segments.append({
                    "global_start": start_time,
                    "global_end": start_time + 30.0,  # 估计值
                    "local_start": 0.0,
                    "local_end": 30.0,
                    "chunk_idx": idx,
                    "has_precise_time": False,  # 无精确时间戳
                    "text": text,
                })

        all_segments.extend(segments)

    if not all_segments:
        return []

    # 按全局开始时间排序
    all_segments.sort(key=lambda x: x["global_start"])

    # 合并重叠区域：对于每个块，跳过开头 overlap_seconds 秒的内容（除非是第一个块）
    # 注意：只有有精确时间戳的片段才能做重叠跳过
    merged = []
    for seg in all_segments:
        chunk_idx = seg["chunk_idx"]
        local_start = seg["local_start"]
        has_precise_time = seg.get("has_precise_time", True)

        # 非第一个块的开头 overlap_seconds 秒是重复内容，跳过
        # 但只有在有精确时间戳时才能跳过
        if chunk_idx > 0 and has_precise_time and local_start < overlap_seconds:
            continue

        merged.append({
            "start": seg["global_start"],
            "end": seg["global_end"],
            "text": seg["text"],
        })

    return merged


def transcribe(
    audio_path: Path,
    model: str = "glm-asr-2512",
    api_key: Optional[str] = None,
    base_url: str = "https://open.bigmodel.cn/api/paas/v4",
    output_format: str = "text",
    output_file: Optional[Path] = None,
    chunk_seconds: float = 30.0,
    overlap_seconds: float = 2.0,
    debug_first_chunk: bool = False,
) -> List[dict]:
    """
    使用智谱AI ASR API 转录音频

    限制：单个音频不超过30秒，仅支持 wav/mp3
    策略：长音频分块处理，每块 30 秒，相邻块有 2 秒重叠
    """
    import requests

    api_key = api_key or os.getenv("ZHIPU_API_KEY")
    if not api_key:
        logger.error("未设置智谱AI API Key，请通过 --api-key 参数或 ZHIPU_API_KEY 环境变量设置")
        sys.exit(1)

    # 检查文件格式（分片会转换为 mp3，所以输入可以是任意 ffmpeg 支持的格式）
    suffix = audio_path.suffix.lower()
    logger.info("输入格式: %s (将切分为 mp3 分片)", suffix)

    # 获取音频时长
    duration = get_audio_duration(audio_path)
    if duration <= 0:
        logger.error("无法获取音频时长")
        sys.exit(1)

    logger.info("音频时长: %.2f 秒", duration)

    # 如果音频不超过30秒，直接转录
    if duration <= chunk_seconds:
        logger.info("上传音频到智谱AI: %s", audio_path)
        started_at = time.perf_counter()

        try:
            result = transcribe_chunk(audio_path, model, api_key, base_url)
        except requests.exceptions.Timeout:
            logger.error("请求超时")
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            logger.error("请求失败: %s", e)
            sys.exit(1)

        elapsed = time.perf_counter() - started_at

        # 解析响应
        results = []
        full_text_lines = []

        if isinstance(result, dict):
            if "segments" in result and isinstance(result["segments"], list):
                for seg in result["segments"]:
                    text = str(seg.get("text", "")).strip()
                    if not text:
                        continue
                    results.append({
                        "start": float(seg.get("start", 0)),
                        "end": float(seg.get("end", 0)),
                        "text": text,
                    })
                    full_text_lines.append(text)
                    logger.info("[%.2f - %.2f] %s", seg.get("start", 0), seg.get("end", 0), text)
            elif "text" in result:
                text = str(result["text"]).strip()
                if text:
                    results.append({"start": 0.0, "end": duration, "text": text})
                    full_text_lines.append(text)
                    logger.info("%s", text)

        logger.info("智谱AI 转录完成: 共 %d 个片段, 耗时 %.2f 秒", len(results), elapsed)

        output_content = format_output(results, full_text_lines, output_format)
        if output_file:
            output_file.write_text(output_content, encoding="utf-8")
            logger.info("结果已保存: %s", output_file)
        else:
            print("\n" + "=" * 50 + "\n")
            print(output_content)

        return results

    # 长音频分块处理
    # 每块 chunk_seconds 秒，步进 (chunk_seconds - overlap_seconds) 秒
    step_seconds = chunk_seconds - overlap_seconds
    chunk_start_times = []
    current_start = 0.0

    while current_start < duration:
        chunk_start_times.append(current_start)
        current_start += step_seconds

    total_chunks = len(chunk_start_times)
    logger.info("音频将分 %d 块处理 (每块 %.0f秒, 重叠 %.0f秒)", total_chunks, chunk_seconds, overlap_seconds)

    if debug_first_chunk:
        logger.info("[DEBUG] 仅处理第一个切片")

    started_at = time.perf_counter()
    chunk_results = []
    temp_dir = tempfile.mkdtemp(prefix="zhipu_asr_")

    # 增量输出文件（用于断点续传）
    progress_file = None
    if output_file:
        progress_file = output_file.with_suffix(output_file.suffix + ".progress")
        logger.info("进度文件: %s", progress_file)

    try:
        for idx, chunk_start in enumerate(chunk_start_times):
            # debug 模式下只处理第一个块
            if debug_first_chunk and idx > 0:
                break
            chunk_duration = min(chunk_seconds, duration - chunk_start)
            chunk_file = Path(temp_dir) / f"chunk_{idx:04d}.mp3"

            logger.info("处理第 %d/%d 块: %.2f - %.2f 秒",
                       idx + 1, total_chunks, chunk_start, chunk_start + chunk_duration)

            # 切割音频
            if not split_audio_chunk(audio_path, chunk_file, chunk_start, chunk_duration):
                logger.error("切割音频块失败: %s", chunk_file)
                continue

            # 转录
            try:
                result = transcribe_chunk(chunk_file, model, api_key, base_url)
                chunk_results.append(result)

                # 显示进度
                if isinstance(result, dict) and "text" in result:
                    preview = result["text"][:50] + "..." if len(result.get("text", "")) > 50 else result.get("text", "")
                    logger.info("  -> %s", preview)

                # 增量写入进度文件
                if progress_file:
                    progress_entry = {
                        "idx": idx,
                        "chunk_start": chunk_start,
                        "chunk_end": chunk_start + chunk_duration,
                        "result": result,
                    }
                    with open(progress_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(progress_entry, ensure_ascii=False) + "\n")

            except Exception as e:
                logger.error("转录块失败: %s", e)
                chunk_results.append({})

    finally:
        # 清理临时文件
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    elapsed = time.perf_counter() - started_at
    logger.info("所有块转录完成，正在合并结果...")

    # 合并结果
    merged_segments = merge_chunk_results(chunk_results, chunk_start_times, overlap_seconds)

    # 提取文本
    full_text_lines = [seg["text"] for seg in merged_segments]
    for seg in merged_segments:
        logger.info("[%.2f - %.2f] %s", seg["start"], seg["end"], seg["text"])

    logger.info("智谱AI 转录完成: 共 %d 个片段, 耗时 %.2f 秒", len(merged_segments), elapsed)

    # 输出结果
    output_content = format_output(merged_segments, full_text_lines, output_format)

    if output_file:
        output_file.write_text(output_content, encoding="utf-8")
        logger.info("结果已保存: %s", output_file)
    else:
        print("\n" + "=" * 50 + "\n")
        print(output_content)

    return merged_segments


def format_output(results: List[dict], full_text_lines: List[str], output_format: str) -> str:
    """格式化输出内容"""
    if output_format == "json":
        return json.dumps(results, ensure_ascii=False, indent=2)
    elif output_format == "srt":
        return format_srt(results)
    elif output_format == "text_with_time":
        lines = [f"[{r['start']:.2f} - {r['end']:.2f}] {r['text']}" for r in results]
        return "\n".join(lines)
    else:  # text
        return "\n".join(full_text_lines)


def format_srt(segments: List[dict]) -> str:
    """格式化为 SRT 字幕格式"""
    lines = []
    for i, seg in enumerate(segments, 1):
        start_time = format_srt_time(seg["start"])
        end_time = format_srt_time(seg["end"])
        lines.append(f"{i}")
        lines.append(f"{start_time} --> {end_time}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)


def format_srt_time(seconds: float) -> str:
    """将秒数转换为 SRT 时间格式"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def main():
    parser = argparse.ArgumentParser(
        description="智谱AI 云端 ASR 转录工具 (GLM-ASR-2512)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s input.m4a
  %(prog)s input.mp3 -o result.txt
  %(prog)s input.wav -f json -o result.json
  %(prog)s input.mp3 --api-key YOUR_API_KEY

环境变量:
  ZHIPU_API_KEY: 智谱AI API Key (必需，也可通过 --api-key 指定)
        """,
    )
    parser.add_argument("input", type=Path, help="输入音频文件 (m4a/mp3/wav 等 ffmpeg 支持的格式)")
    parser.add_argument("-o", "--output", type=Path, help="输出文件路径")
    parser.add_argument("--model", default="glm-asr-2512", help="模型名称 (默认: glm-asr-2512)")
    parser.add_argument("--api-key", help="智谱AI API Key (或设置 ZHIPU_API_KEY 环境变量)")
    parser.add_argument("--api-base-url", default="https://open.bigmodel.cn/api/paas/v4", help="智谱AI API 地址")
    parser.add_argument("--debug-first-chunk", action="store_true", help="仅处理第一个切片 (用于调试)")
    parser.add_argument(
        "--format", "-f",
        choices=["text", "text_with_time", "json", "srt"],
        default="text",
        help="输出格式 (默认: text)",
    )

    args = parser.parse_args()

    if not args.input.exists():
        logger.error("输入文件不存在: %s", args.input)
        sys.exit(1)

    transcribe(
        audio_path=args.input,
        model=args.model,
        api_key=args.api_key,
        base_url=args.api_base_url,
        output_format=args.format,
        output_file=args.output,
        debug_first_chunk=args.debug_first_chunk,
    )


if __name__ == "__main__":
    main()
