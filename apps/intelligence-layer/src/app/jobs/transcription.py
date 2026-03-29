"""
app/jobs/transcription.py — Audio transcription job.

Downloads audio, chunks into segments, transcribes via Whisper or Deepgram,
stores full transcript in Redis, then chains into meeting summary.
"""
from __future__ import annotations

import logging
import struct
from typing import Any

from pydantic import BaseModel

from app.jobs.enqueue import JobContext, enqueue_meeting_summary
from app.jobs.observability import JobTracer
from app.jobs.retry import with_retry_policy
from app.models.access_scope import AccessScope

logger = logging.getLogger("sidecar.jobs.transcription")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SEGMENT_SECONDS = 1200  # 20 minutes
OVERLAP_SECONDS = 30
TRANSCRIPT_TTL_S = 172_800  # 48 hours
SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2  # 16-bit PCM
NUM_CHANNELS = 1


# ---------------------------------------------------------------------------
# Local models (not in schemas.py)
# ---------------------------------------------------------------------------


class TranscriptSegment(BaseModel):
    """A single segment of transcribed audio."""
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str | None = None
    confidence: float = 1.0


class TranscriptionResult(BaseModel):
    """Full transcription result."""
    meeting_id: str
    duration_seconds: float
    segments: list[TranscriptSegment]
    full_text: str
    language: str = "en"
    provider: str = "whisper"


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def _make_wav_header(
    data_size: int,
    sample_rate: int = SAMPLE_RATE,
    bytes_per_sample: int = BYTES_PER_SAMPLE,
) -> bytes:
    """Construct a minimal WAV header for mono PCM."""
    channels = NUM_CHANNELS
    byte_rate = sample_rate * channels * bytes_per_sample
    block_align = channels * bytes_per_sample
    bits_per_sample = bytes_per_sample * 8

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header


def _chunk_audio_bytes(
    audio_bytes: bytes,
    total_duration: int,
    sample_rate: int = SAMPLE_RATE,
    bytes_per_sample: int = BYTES_PER_SAMPLE,
) -> list[tuple[int, bytes, float, float]]:
    """Split audio bytes into overlapping segments.

    Returns list of (index, chunk_wav, start_seconds,
    end_seconds).
    """
    if total_duration <= MAX_SEGMENT_SECONDS:
        return [
            (0, audio_bytes, 0.0, float(total_duration))
        ]

    bytes_per_second = sample_rate * bytes_per_sample
    segment_bytes = MAX_SEGMENT_SECONDS * bytes_per_second
    overlap_bytes = OVERLAP_SECONDS * bytes_per_second

    # Skip WAV header (44 bytes) for raw PCM chunking.
    pcm_data = audio_bytes[44:]

    chunks: list[tuple[int, bytes, float, float]] = []
    offset = 0
    index = 0

    while offset < len(pcm_data):
        end = min(
            offset + segment_bytes, len(pcm_data)
        )
        chunk_pcm = pcm_data[offset:end]

        start_sec = offset / bytes_per_second
        end_sec = end / bytes_per_second

        chunk_wav = (
            _make_wav_header(
                len(chunk_pcm),
                sample_rate,
                bytes_per_sample,
            )
            + chunk_pcm
        )
        chunks.append(
            (index, chunk_wav, start_sec, end_sec)
        )

        offset = (
            end - overlap_bytes
            if end < len(pcm_data)
            else end
        )
        index += 1

    return chunks


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


async def _download_audio(
    http_client: Any,
    platform: Any,
    audio_object_key: str,
    access_scope: AccessScope,
) -> bytes:
    """
    Download audio from the platform's object store.

    Tries the platform client's presigned URL endpoint first,
    then falls back to a direct HTTP GET.
    """
    try:
        # Try platform presigned URL approach
        url = await platform.get_audio_download_url(audio_object_key, access_scope)
        resp = await http_client.get(url)
        resp.raise_for_status()
        return resp.content
    except (AttributeError, Exception):
        # Fallback: direct download via platform API
        resp = await http_client.get(
            f"{platform._config.base_url}/v1/audio/{audio_object_key}",
            headers={
                "Authorization": f"Bearer {platform._config.service_token}",
                "X-Access-Scope": access_scope.model_dump_json(),
            },
        )
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Transcription backends
# ---------------------------------------------------------------------------


async def _transcribe_segment_whisper(
    http_client: Any,
    wav_bytes: bytes,
    settings: Any,
    start_offset: float = 0.0,
) -> list[TranscriptSegment]:
    """Transcribe a WAV segment using OpenAI Whisper API."""
    import httpx

    form_data = {
        "model": settings.whisper_model if hasattr(settings, "whisper_model") else "whisper-1",
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }

    files = {"file": ("segment.wav", wav_bytes, "audio/wav")}

    resp = await http_client.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        data=form_data,
        files=files,
        timeout=httpx.Timeout(300.0),
    )
    resp.raise_for_status()
    data = resp.json()

    segments: list[TranscriptSegment] = []
    for seg in data.get("segments", []):
        segments.append(TranscriptSegment(
            start_seconds=start_offset + seg.get("start", 0.0),
            end_seconds=start_offset + seg.get("end", 0.0),
            text=seg.get("text", "").strip(),
            confidence=seg.get("avg_logprob", 0.0),
        ))

    # If no segments returned, use the full text
    if not segments and data.get("text"):
        segments.append(TranscriptSegment(
            start_seconds=start_offset,
            end_seconds=start_offset + data.get("duration", 0.0),
            text=data["text"].strip(),
        ))

    return segments


async def _transcribe_segment_deepgram(
    http_client: Any,
    wav_bytes: bytes,
    settings: Any,
    start_offset: float = 0.0,
) -> list[TranscriptSegment]:
    """Transcribe a WAV segment using Deepgram API."""
    import httpx

    resp = await http_client.post(
        "https://api.deepgram.com/v1/listen",
        headers={
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": "audio/wav",
        },
        params={
            "model": "nova-2",
            "smart_format": "true",
            "diarize": "true",
            "utterances": "true",
        },
        content=wav_bytes,
        timeout=httpx.Timeout(300.0),
    )
    resp.raise_for_status()
    data = resp.json()

    segments: list[TranscriptSegment] = []
    results = data.get("results", {})

    # Use utterances if available for speaker diarization
    utterances = results.get("utterances", [])
    if utterances:
        for utt in utterances:
            segments.append(TranscriptSegment(
                start_seconds=start_offset + utt.get("start", 0.0),
                end_seconds=start_offset + utt.get("end", 0.0),
                text=utt.get("transcript", "").strip(),
                speaker=f"Speaker {utt.get('speaker', 0)}",
                confidence=utt.get("confidence", 1.0),
            ))
    else:
        # Fall back to channel alternatives
        channels = results.get("channels", [])
        for channel in channels:
            for alt in channel.get("alternatives", []):
                transcript = alt.get("transcript", "").strip()
                if transcript:
                    segments.append(TranscriptSegment(
                        start_seconds=start_offset,
                        end_seconds=start_offset + (data.get("metadata", {}).get("duration", 0.0)),
                        text=transcript,
                        confidence=alt.get("confidence", 1.0),
                    ))

    return segments


# ---------------------------------------------------------------------------
# Job entry point
# ---------------------------------------------------------------------------


@with_retry_policy
async def run_transcription(
    ctx: dict[str, Any],
    job_ctx_raw: dict | None = None,
    meeting_id: str | None = None,
    audio_object_key: str | None = None,
    audio_duration_seconds: int | None = None,
) -> dict:
    """
    Transcribe meeting audio.

    Downloads audio, chunks it, transcribes each segment, assembles
    full transcript, stores in Redis, and enqueues meeting summary.
    """
    if job_ctx_raw is None:
        raise ValueError("run_transcription requires job_ctx_raw")
    if not meeting_id or not audio_object_key:
        raise ValueError("meeting_id and audio_object_key are required")

    job_ctx = JobContext(**job_ctx_raw)
    access_scope = AccessScope(**job_ctx.access_scope)

    platform = ctx["platform_client"]
    redis = ctx["redis"]
    http_client = ctx.get("http_client")
    settings = ctx.get("settings")
    langfuse = ctx.get("langfuse")

    tracer: JobTracer | None = None
    if langfuse:
        tracer = JobTracer(
            langfuse=langfuse,
            job_name="transcription",
            tenant_id=job_ctx.tenant_id,
            actor_id=job_ctx.actor_id,
            extra_metadata={"meeting_id": meeting_id},
        )

    try:
        # Choose transcription backend
        provider = "whisper"
        if settings and hasattr(settings, "transcription_provider"):
            provider = settings.transcription_provider

        transcribe_fn = (
            _transcribe_segment_whisper if provider == "whisper"
            else _transcribe_segment_deepgram
        )

        # Download audio
        span = tracer.start_span(name="download_audio") if tracer else None
        if http_client is None:
            import httpx
            http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

        audio_bytes = await _download_audio(http_client, platform, audio_object_key, access_scope)
        if tracer:
            tracer.record_platform_read()
        if span:
            span.end(output={"size_bytes": len(audio_bytes)})

        logger.info(
            "transcription: downloaded %d bytes for meeting %s",
            len(audio_bytes),
            meeting_id,
        )

        # Chunk audio
        chunks = _chunk_audio_bytes(
            audio_bytes, audio_duration_seconds
        )
        logger.info(
            "transcription: split into %d segments for meeting %s",
            len(chunks),
            meeting_id,
        )

        # Transcribe each segment
        all_segments: list[TranscriptSegment] = []
        for i, wav_bytes, start_s, end_s in chunks:
            seg_span = tracer.start_span(
                name=f"transcribe_segment_{i}",
                input={"start": start_s, "end": end_s},
            ) if tracer else None

            segments = await transcribe_fn(
                http_client, wav_bytes, settings, start_offset=start_s,
            )
            all_segments.extend(segments)

            if seg_span:
                seg_span.end(output={"segments": len(segments)})

        # Deduplicate overlapping segments
        all_segments.sort(key=lambda s: s.start_seconds)
        deduped: list[TranscriptSegment] = []
        for seg in all_segments:
            if deduped and seg.start_seconds < deduped[-1].end_seconds:
                # Overlapping: keep the one with more text
                if len(seg.text) > len(deduped[-1].text):
                    deduped[-1] = seg
            else:
                deduped.append(seg)

        # Build full transcript
        full_text = "\n".join(
            f"[{seg.speaker or 'Speaker'}] {seg.text}" if seg.speaker
            else seg.text
            for seg in deduped
        )

        total_duration = audio_duration_seconds or (
            deduped[-1].end_seconds if deduped else 0.0
        )

        result = TranscriptionResult(
            meeting_id=meeting_id,
            duration_seconds=total_duration,
            segments=deduped,
            full_text=full_text,
            language="en",
            provider=provider,
        )

        # Store transcript in Redis
        transcript_key = f"sidecar:transcript:{job_ctx.tenant_id}:{meeting_id}"
        await redis.set(
            transcript_key,
            result.model_dump_json(),
            ex=TRANSCRIPT_TTL_S,
        )

        logger.info(
            "transcription: completed meeting %s — %d segments, %.0fs duration",
            meeting_id,
            len(deduped),
            total_duration,
        )

        # Chain: enqueue meeting summary
        summary_job_id = await enqueue_meeting_summary(
            job_ctx=job_ctx,
            meeting_id=meeting_id,
            transcript_key=transcript_key,
        )

        if tracer:
            tracer.complete(output={
                "meeting_id": meeting_id,
                "segments": len(deduped),
                "duration_seconds": total_duration,
                "summary_job_id": summary_job_id,
            })

        return {
            "status": "transcribed",
            "meeting_id": meeting_id,
            "segments": len(deduped),
            "duration_seconds": total_duration,
            "transcript_key": transcript_key,
            "summary_job_id": summary_job_id,
        }

    except Exception as exc:
        if tracer:
            tracer.fail(exc, category="transcription_error")
        raise
