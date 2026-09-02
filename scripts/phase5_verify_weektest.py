#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from joanna.core.phase5_verification import (
    DEFAULT_IMPORT_DIR,
    Phase5VerificationExpectations,
    dumps_verification,
    verify_phase5_weektest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify phase 5 DJI/Apple Health timeline closure without mutating data.")
    parser.add_argument("--db", default=".joanna/phase5-weektest/phase5-weektest.db")
    parser.add_argument("--import-dir", default=str(DEFAULT_IMPORT_DIR))
    parser.add_argument("--expected-audio-segments", type=int, default=12)
    parser.add_argument("--expected-apple-health-events", type=int, default=4449)
    parser.add_argument("--expected-qwen-audits", type=int, default=12)
    parser.add_argument("--expected-qwen-slices", type=int, default=36)
    parser.add_argument("--qwen-region", default="beijing")
    args = parser.parse_args()

    expected = Phase5VerificationExpectations(
        audio_segments=args.expected_audio_segments,
        audio_transcripts=args.expected_audio_segments,
        audio_features=args.expected_audio_segments,
        active_audio_scene=args.expected_audio_segments,
        apple_health_events=args.expected_apple_health_events,
        qwen_audits=args.expected_qwen_audits,
        qwen_slice_count=args.expected_qwen_slices,
        qwen_region=args.qwen_region,
    )
    result = verify_phase5_weektest(
        db_path=Path(args.db),
        import_dir=Path(args.import_dir),
        expectations=expected,
    )
    print(dumps_verification(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
