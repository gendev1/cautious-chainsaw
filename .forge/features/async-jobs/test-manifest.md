# Test Manifest: Async Jobs

## Test Files Created

| File | Tests | Purpose |
|---|---|---|
| `tests/test_job_errors.py` | 6 | FailureCategory classification + retry delay computation |
| `tests/test_job_retry.py` | 3 | with_retry_policy decorator (success/retry/dead-letter) |
| `tests/test_observability.py` | 3 | JobMetrics + JobTracer with real Langfuse |
| `tests/test_enqueue.py` | 2 | JobContext serialization + validation |
| `tests/test_transcription_helpers.py` | 4 | Audio chunking + WAV header construction |
| `tests/test_meeting_summary_helpers.py` | 2 | Transcript truncation logic |
| `tests/test_rag_index_helpers.py` | 3 | Text chunking + deterministic chunk IDs |

## Spec → Test Mapping

| Spec Requirement | Test Location |
|---|---|
| FailureCategory enum | test_job_errors.py |
| classify_error HTTP 500 → PLATFORM_READ | test_job_errors.py::test_classify_http_500_as_platform_read |
| classify_error timeout → PLATFORM_READ | test_job_errors.py::test_classify_timeout_as_platform_read |
| classify_error 429 → MODEL_PROVIDER | test_job_errors.py::test_classify_rate_limit_as_model_provider |
| classify_error ValueError → VALIDATION | test_job_errors.py::test_classify_value_error_as_validation |
| compute_retry_delay validation = None | test_job_errors.py::test_compute_retry_delay_validation_returns_none |
| compute_retry_delay exponential backoff | test_job_errors.py::test_compute_retry_delay_platform_read_backoff |
| with_retry_policy passthrough | test_job_retry.py::test_passthrough_on_success |
| with_retry_policy raises Retry | test_job_retry.py::test_raises_retry_on_retryable_error |
| with_retry_policy dead letter | test_job_retry.py::test_dead_letter_on_non_retryable |
| JobMetrics duration tracking | test_observability.py::test_job_metrics_duration |
| JobTracer token accumulation | test_observability.py::test_job_tracer_accumulates_tokens |
| JobContext serialization | test_enqueue.py::test_job_context_serialization |
| Audio chunking short → 1 chunk | test_transcription_helpers.py::test_short_audio_single_chunk |
| Audio chunking long → multiple | test_transcription_helpers.py::test_long_audio_multiple_chunks |
| WAV header 44 bytes | test_transcription_helpers.py::test_wav_header_size |
| Transcript truncation short | test_meeting_summary_helpers.py::test_short_transcript_unchanged |
| Transcript truncation head/tail | test_meeting_summary_helpers.py::test_long_transcript_truncated_with_marker |
| chunk_text short → 1 | test_rag_index_helpers.py::test_short_text_single_chunk |
| chunk_text paragraph splitting | test_rag_index_helpers.py::test_long_text_splits_at_paragraphs |
| make_chunk_id deterministic | test_rag_index_helpers.py::test_make_chunk_id_deterministic |

## Edge Cases Covered

- Non-retryable errors skip retry and go to dead letter
- WAV header magic bytes verification
- Audio shorter than segment size returns single chunk
- Transcript under context limit returned unchanged
- Chunk ID collision prevention (different index → different ID)

## Test File Checksums

| File | SHA256 |
|---|---|
| tests/test_job_errors.py | e5881ecbe88b4336c8c4b7eae41337ed3bad5b4d1a5a898415d7a0d8f0784c08 |
| tests/test_job_retry.py | f2a0b9d7b88c6b0699742539a5eae0104f018ac45780387f965bf0617b53a211 |
| tests/test_observability.py | ba85dee9cfe145a41fdcbf14ee960e0522f29ac9f83f57ff636e8849743e6401 |
| tests/test_enqueue.py | adbab232c4f9ab528a7b79334e984931f4a6a18b74f8f56824845669ff8fdc80 |
| tests/test_transcription_helpers.py | 06b03ab9dfcc4b672dadf875d426d6e1e0e221c6e3a9a534cca9cc332c0be21d |
| tests/test_meeting_summary_helpers.py | b5804381d4073c24b30f865b596df5746c7ccaa7d09c6a6d526750ca65680fb5 |
| tests/test_rag_index_helpers.py | a7601503b157c303ef46872d680e1f418c8b4784ca227b42bc92854d40609a98 |

## Run Command

```bash
cd apps/intelligence-layer && python -m pytest tests/test_job_errors.py tests/test_job_retry.py tests/test_observability.py tests/test_enqueue.py tests/test_transcription_helpers.py tests/test_meeting_summary_helpers.py tests/test_rag_index_helpers.py -v
```
