"""对已有 evidence.json 重新做 OCR/ASR 融合，不重新解析视频。

用法：
    bash scripts/dev.sh fuse data/output/bilibili/BV.../evidence.json
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from app.contracts import EvidencePacket, Modality
from app.ingest.evidence import normalize_ocr_text
from app.ingest.fusion import fuse_ocr_asr

from export_evidence import write_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="对已有证据做跨模态融合")
    parser.add_argument("evidence_json", help="evidence.json 路径")
    parser.add_argument("--min-similarity", type=float, default=0.55)
    parser.add_argument("--min-confidence", type=float, default=0.7)
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    path = Path(args.evidence_json)
    payload = json.loads(path.read_text(encoding="utf-8"))
    evidence = [EvidencePacket.model_validate(item) for item in payload["evidence"]]
    evidence = [
        item.model_copy(update={"text": normalize_ocr_text(item.text)})
        if item.modality == Modality.OCR
        else item
        for item in evidence
    ]
    fused, stats = fuse_ocr_asr(
        evidence,
        min_similarity=args.min_similarity,
        min_confidence=args.min_confidence,
    )
    out_dir = path.parent
    write_outputs(
        out_dir,
        url=payload["url"],
        source=payload["source"],
        video_key=payload["source_video_id"],
        evidence=fused,
        stats={
            **payload.get("stats", {}),
            **stats.as_dict(),
            "evidence_count": len(fused),
        },
        warnings=payload.get("warnings", []),
        elapsed=float(payload.get("elapsed_seconds", 0)),
        prefix="fused",
    )
    print(f"before : {len(evidence)}")
    print(f"after  : {len(fused)}")
    print(f"fused  : {stats.fused_ocr_blocks} OCR blocks / consumed {stats.consumed_asr_cues} ASR cues")
    print(f"output : {out_dir / 'fused.md'}")
    print(f"         {out_dir / 'fused.csv'}")
    print(f"         {out_dir / 'fused.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
