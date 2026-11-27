#!/usr/bin/env bash
# Renders two-voices.wav: two macOS voices taking turns (Daniel, Samantha, Daniel), then both at once, as 16 kHz mono
# PCM, the form phase 1 extracts. The WAV is kept in git, so the diarization test runs on any system; this script is
# needed only to render it again, and only on a Mac. Every sentence is invented.
set -euo pipefail
cd "$(dirname "$0")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

say -v Daniel -o "$work/1.aiff" "Good morning. Today we plan the rollout of AcmeSync to the gateway team, and I will walk you through the three steps we agreed on last week."
say -v Samantha -o "$work/2.aiff" "Before you start, what happens to the existing database? The gateway team still writes to it every night, and nobody has told them about a migration."
say -v Daniel -o "$work/3.aiff" "The database stays where it is for now. We move it only after the rollout is stable, which should take about two weeks."
say -v Samantha -o "$work/4a.aiff" "That sounds reasonable to me, let us write it down."
say -v Daniel -o "$work/4b.aiff" "Good, then we agree on the plan and we start on Monday."

for name in 1 2 3 4a 4b; do
    ffmpeg -loglevel error -y -i "$work/$name.aiff" -ar 16000 -ac 1 "$work/$name.wav"
done
ffmpeg -loglevel error -y -f lavfi -i anullsrc=r=16000:cl=mono -t 0.8 "$work/gap.wav"
# The last two sentences are said at once: the overlap the diarization must leave without a speaker
ffmpeg -loglevel error -y -i "$work/4a.wav" -i "$work/4b.wav" -filter_complex amix=inputs=2:duration=longest "$work/4.wav"
printf "file '%s'\n" "$work/1.wav" "$work/gap.wav" "$work/2.wav" "$work/gap.wav" "$work/3.wav" "$work/gap.wav" \
    "$work/4.wav" > "$work/list.txt"
ffmpeg -loglevel error -y -f concat -safe 0 -i "$work/list.txt" -ar 16000 -ac 1 -c:a pcm_s16le two-voices.wav
