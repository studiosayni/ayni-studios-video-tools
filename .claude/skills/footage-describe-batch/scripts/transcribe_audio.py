#!/usr/bin/env python3
"""
Local speech-to-text via mlx-whisper (Apple Silicon, on-device, no upload).

Uses the Python API rather than the `mlx_whisper` CLI on purpose: the CLI
collapses Whisper's temperature fallback to a single fixed value and defaults
to conditioning each chunk on the previous chunk's text. That combination is
a known trigger for repetition-loop hallucinations on long files (confirmed
firsthand: a 12-minute loop on a 19-minute recording). This script restores
the fallback ladder and disables that conditioning.

Usage:
    venv/bin/python transcribe_audio.py <audio_path> [options]

Examples:
    venv/bin/python transcribe_audio.py ~/Desktop/interview.mp3 --language es
    venv/bin/python transcribe_audio.py ~/Desktop/talk.m4a --language en \\
        --output-dir ~/Desktop/talk_transcripts
"""
import argparse
import json
from pathlib import Path

import mlx_whisper
from mlx_whisper.writers import get_writer


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio", help="Path to the audio/video file")
    parser.add_argument("--language", default=None, help="ISO code, e.g. es, en. Omit to auto-detect (less reliable).")
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-mlx", help="HF repo for the MLX whisper model")
    parser.add_argument("--output-dir", default=None, help="Defaults to <audio_dir>/transcripts")
    parser.add_argument("--output-name", default=None, help="Defaults to <audio stem>_<language>")
    args = parser.parse_args()

    audio_path = Path(args.audio).expanduser().resolve()
    if not audio_path.exists():
        raise SystemExit(f"Audio file not found: {audio_path}")

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else audio_path.parent / "transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = args.output_name or f"{audio_path.stem}_{args.language or 'auto'}"

    print(f"Transcribing {audio_path.name} -> {output_dir}/{output_name}.*", flush=True)

    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=args.model,
        language=args.language,
        task="transcribe",
        temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),  # fallback ladder — do not collapse to a single float
        condition_on_previous_text=False,              # avoids cascading repeat-loops on long files
        compression_ratio_threshold=2.4,
        logprob_threshold=-1.0,
        no_speech_threshold=0.6,
        word_timestamps=True,
        hallucination_silence_threshold=2.0,
        verbose=True,
    )

    with open(output_dir / f"{output_name}.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    for fmt in ("srt", "txt", "vtt"):
        writer = get_writer(fmt, str(output_dir))
        writer(result, output_name, {
            "max_line_width": None,
            "max_line_count": None,
            "max_words_per_line": None,
            "highlight_words": False,
        })

    # QA pass: surface low-confidence words/segments for review rather than
    # silently trusting them. Most low-probability hits are ordinary function
    # words (an artifact of subword tokenization) — treat this as a candidate
    # list to read through, not a list of confirmed errors.
    flags = []
    for seg in result["segments"]:
        for w in seg.get("words", []):
            if w["probability"] < 0.5:
                flags.append((w["start"], w["word"].strip(), w["probability"], seg["text"].strip()))

    qa_path = output_dir / f"{output_name}_qa_flags.txt"
    with open(qa_path, "w", encoding="utf-8") as f:
        for t, word, p, ctx in flags:
            m, s = divmod(t, 60)
            f.write(f"{int(m):02d}:{s:05.2f}  p={p:.2f}  '{word}'  | {ctx}\n")

    n = len(result["segments"])
    dur = result["segments"][-1]["end"] if result["segments"] else 0
    print(f"Done: {n} segments, {dur:.0f}s covered.")
    print(f"Wrote {output_name}.{{json,srt,txt,vtt}} and {qa_path.name} to {output_dir}")


if __name__ == "__main__":
    main()
