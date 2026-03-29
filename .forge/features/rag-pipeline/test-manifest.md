# Test Manifest: RAG Pipeline

## Test Files Created

| File | Test count | Purpose |
|---|---|---|
| `tests/test_chunking.py` | 5 | Token chunking, overlap, metadata, edge cases |
| `tests/test_reranking.py` | 5 | Composite scoring, recency decay, association, top-K |
| `tests/test_context.py` | 4 | Token budgeting, truncation priority, budget calc |
| `tests/test_citations.py` | 4 | Deduplication, excerpt truncation, metadata |
| `tests/test_embeddings.py` | 6 | Normalization, batch splitting, settings |

**Total: 5 files, 24 test cases** (pure module tests; integration tests added during implementation)

## Spec → Test Mapping

| Spec case | Test location |
|---|---|
| T1: Chunker splits text | `test_chunking.py::test_chunker_splits_long_text` |
| T2: Chunker preserves metadata | `test_chunking.py::test_chunker_preserves_metadata` |
| T3: Reranker sorts by score | `test_reranking.py::test_reranker_sorts_by_composite_score` |
| T4: Recency decay | `test_reranking.py::test_recency_decay` |
| T5: Association scoring | `test_reranking.py::test_association_score_client_match` |
| T6: Context fits budget | `test_context.py::test_context_fits_within_budget` |
| T7: Truncates oldest history | `test_context.py::test_truncates_oldest_history_first` |
| T8: Citation deduplication | `test_citations.py::test_deduplicates_by_source` |
| T9: Excerpt truncation | `test_citations.py::test_excerpt_truncated_to_200_chars` |
| T10: Batch splitting | `test_embeddings.py::test_batch_splitting` |
| T14: Normalization | `test_embeddings.py::test_verify_normalized_*` |

## Edge Cases Covered

- [x] Empty text produces no chunks
- [x] Short text fits in single chunk
- [x] Sequential chunk indexes
- [x] Empty reranking input
- [x] Top-K limits output
- [x] No chunks produces "no context" message
- [x] Zero vector normalization
- [x] Citation from metadata title

## Test File Checksums

| File | SHA256 |
|---|---|
| `apps/intelligence-layer/tests/test_chunking.py` | `04c6c45bdd9e460daa140d77092c46de9dfde4fbfa8ebdccb0226f08d13c4a1e` |
| `apps/intelligence-layer/tests/test_reranking.py` | `1f747e60b7f4a1d7d9c84032237d335a05e8eb5b064ccbfcaf9099b0b430a4b8` |
| `apps/intelligence-layer/tests/test_context.py` | `14e2291ab00010d2db189fe55b360d9b9420fa952dc7e5e34dc18ec769d12ebf` |
| `apps/intelligence-layer/tests/test_citations.py` | `62b4ae20575af5bc5d7f74426bc2fdf2882177af9d2249aa660637a0c4babf43` |
| `apps/intelligence-layer/tests/test_embeddings.py` | `aa242e04a5d341d7d70947ccebd07b0f056b15aa1c16e08af9e2bc5c9578cf53` |

## Run Command

```bash
cd apps/intelligence-layer && uv run pytest tests/ -v
```
