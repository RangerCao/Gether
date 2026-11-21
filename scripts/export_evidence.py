"""解析单个视频并导出可人工检查的证据切片。

用法（在仓库根目录）：
    bash scripts/dev.sh export "https://www.bilibili.com/video/BV1..."
    bash scripts/dev.sh export BV1... --no-ocr --asr-model base --beam-size 1
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

from app.contracts import EvidencePacket
from app.core.config import PROJECT_ROOT, Settings
from app.ingest.pipeline import IngestError, IngestPipeline
from app.ingest.sources.registry import SourceRegistry


def timestamp(ms: int, *, with_ms: bool = True) -> str:
    total_seconds, millis = divmod(max(ms, 0), 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    base = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
    return f"{base}.{millis:03d}" if with_ms else base


def jump_url(url: str, start_ms: int, source: str) -> str:
    seconds = start_ms // 1000
    if source == "youtube":
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}t={seconds}s"
    if source == "bilibili":
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}t={seconds}"
    return url


def write_outputs(
    out_dir: Path,
    *,
    url: str,
    source: str,
    video_key: str,
    evidence: list[EvidencePacket],
    stats: dict,
    warnings: list[str],
    elapsed: float,
    prefix: str = "evidence",
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "source": source,
        "source_video_id": video_key,
        "url": url,
        "elapsed_seconds": round(elapsed, 1),
        "evidence_count": len(evidence),
        "stats": stats,
        "warnings": warnings,
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }
    (out_dir / f"{prefix}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# 证据切片：{video_key}",
        "",
        f"- 来源：{source}",
        f"- 原视频：{url}",
        f"- 切片数：**{len(evidence)}**",
        f"- 解析耗时：{elapsed:.1f}s",
        f"- 统计：`{json.dumps(stats, ensure_ascii=False)}`",
    ]
    if warnings:
        lines += ["", "**警告**："] + [f"- {item}" for item in warnings]
    lines += [
        "",
        "| # | 起点 | 终点 | 模态 | 来源 | 文本 | 跳转 |",
        "|---:|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(evidence, start=1):
        link = jump_url(url, item.start_ms, source)
        text = item.text.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {index} | {timestamp(item.start_ms)} | {timestamp(item.end_ms)} | "
            f"{item.modality.value} | {item.source_type.value} | {text} | [播放]({link}) |"
        )
    (out_dir / f"{prefix}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    with (out_dir / f"{prefix}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "index",
                "start_ms",
                "end_ms",
                "start_hms",
                "end_hms",
                "modality",
                "source_type",
                "confidence",
                "text",
                "raw_text",
                "frame_ref",
                "jump_url",
            ]
        )
        for index, item in enumerate(evidence, start=1):
            writer.writerow(
                [
                    index,
                    item.start_ms,
                    item.end_ms,
                    timestamp(item.start_ms),
                    timestamp(item.end_ms),
                    item.modality.value,
                    item.source_type.value,
                    item.confidence,
                    item.text,
                    item.raw_text or "",
                    item.frame_ref or "",
                    jump_url(url, item.start_ms, source),
                ]
            )


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    updates: dict = {}
    if args.no_ocr:
        updates["ocr_enabled"] = False
    if args.asr_model:
        updates["asr_model"] = args.asr_model
    if args.beam_size:
        updates["asr_beam_size"] = args.beam_size
    if args.hotwords:
        updates["asr_hotwords"] = args.hotwords
    if args.initial_prompt:
        updates["asr_initial_prompt"] = args.initial_prompt
    if updates:
        settings = settings.model_copy(update=updates)

    registry = SourceRegistry(settings)
    ref = await registry.normalize(args.url)
    task_id = f"preview-{uuid4().hex[:8]}"
    video_id = f"preview-{ref.source.value}-{ref.source_video_id}"

    print(f"source : {ref.source.value}")
    print(f"video  : {ref.source_video_id}")
    print(f"url    : {ref.url}")
    print(f"ocr    : {settings.ocr_enabled} | asr: {settings.asr_model} ({settings.asr_device})")

    started = time.perf_counter()
    pipeline = IngestPipeline(settings)
    try:
        result = await pipeline.parse_video(
            task_id=task_id,
            video_id=video_id,
            ref=ref,
            progress=lambda stage, current, total, message: print(
                f"  [{stage}] {current}/{total} {message}"
            ),
        )
    except IngestError as exc:
        print(f"解析失败 [{exc.code}] {exc.message}", file=sys.stderr)
        return 1
    finally:
        await pipeline.aclose()

    elapsed = time.perf_counter() - started
    out_dir = Path(args.out_dir) / ref.source.value / ref.source_video_id
    write_outputs(
        out_dir,
        url=ref.url,
        source=ref.source.value,
        video_key=ref.source_video_id,
        evidence=result.evidence,
        stats=result.stats,
        warnings=result.warnings,
        elapsed=elapsed,
    )
    print("")
    print(f"evidence: {len(result.evidence)}")
    print(f"elapsed : {elapsed:.1f}s")
    print(f"output  : {out_dir}")
    print(f"  - {out_dir / 'evidence.md'}")
    print(f"  - {out_dir / 'evidence.csv'}")
    print(f"  - {out_dir / 'evidence.json'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="解析单个视频并导出证据切片")
    parser.add_argument("url", help="B站/YouTube 链接，或本地视频路径")
    parser.add_argument(
        "--out-dir",
        default=str(PROJECT_ROOT / "data" / "output"),
        help="输出目录（默认 data/output）",
    )
    parser.add_argument("--no-ocr", action="store_true", help="关闭 OCR，只做字幕/ASR")
    parser.add_argument("--asr-model", default=None, help="覆盖 ASR 模型，如 base/small/medium")
    parser.add_argument("--beam-size", type=int, default=None, help="覆盖 beam size")
    parser.add_argument("--hotwords", default=None, help="领域热词，逗号分隔")
    parser.add_argument("--initial-prompt", default=None, help="覆盖 ASR initial prompt")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
