"""Tests for transcription helper functions."""
from __future__ import annotations

from app.jobs.transcription import (
    MAX_SEGMENT_SECONDS,
    _chunk_audio_bytes,
    _make_wav_header,
)


def test_short_audio_single_chunk() -> None:
    """Audio shorter than MAX_SEGMENT_SECONDS returns one chunk."""
    audio = b"RIFF" + b"\x00" * 40 + b"\x00" * 1000
    chunks = _chunk_audio_bytes(audio, total_duration=60)
    assert len(chunks) == 1
    assert chunks[0][0] == 0  # index
    assert chunks[0][2] == 0.0  # start_seconds


def test_long_audio_multiple_chunks() -> None:
    """Audio longer than MAX_SEGMENT_SECONDS produces multiple chunks."""
    sample_rate = 16000
    bytes_per_sample = 2
    duration = MAX_SEGMENT_SECONDS + 600  # 1800s total
    pcm_size = duration * sample_rate * bytes_per_sample
    header = b"RIFF" + b"\x00" * 40
    audio = header + b"\x00" * pcm_size
    chunks = _chunk_audio_bytes(audio, total_duration=duration)
    assert len(chunks) >= 2


def test_wav_header_size() -> None:
    """WAV header is exactly 44 bytes."""
    header = _make_wav_header(1000, 16000, 2)
    assert len(header) == 44


def test_wav_header_starts_with_riff() -> None:
    """WAV header starts with RIFF magic bytes."""
    header = _make_wav_header(1000, 16000, 2)
    assert header[:4] == b"RIFF"
