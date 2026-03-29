# Spike Report: RAG Pipeline

## Dependencies Explored

| Dependency | Status | Notes |
|---|---|---|
| asyncpg | Needs adding | Postgres async driver |
| tiktoken | Needs adding | Token counting for chunking |
| numpy | Needs adding | Embedding normalization |
| Docker pgvector | Available | `pgvector/pgvector:pg16` image pulled, Postgres 16.13 |
| Docker Compose | Available | v2.40.0 |

## Scratch Files

None — Docker pgvector image confirmed working, all Python packages are well-documented.
