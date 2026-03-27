# Epic 13: Reporting, Statements, and Snapshots

## Goal

Generate client-ready reports and statements from frozen snapshot inputs, store them as immutable versioned artifacts, and provide async generation and retrieval APIs. Published reports must never depend on mutable live reads. All report inputs are frozen at snapshot time, all artifacts are immutable once published, and all generation is asynchronous.

## Dependencies

- Epic 5 (Document Vault and Records Management) -- artifact storage patterns, object storage integration, metadata model
- Epic 11 (Cash, Ledger, and Balance Projections) -- cash balances, ledger entries, settled vs pending amounts
- Epic 12 (Billing and Fee Operations) -- fee schedules, billing runs, invoice records for billing statements

## Architecture Notes

### Storage split (per data-architecture spec)

| Layer | What lives there |
|---|---|
| Postgres | Report definitions, report jobs, snapshot metadata, artifact references, publication status, generation audit trail |
| Object storage | Published PDFs, generated statements, downloadable report files |
| MongoDB (optional) | Rich structured snapshot payloads, denormalized performance payloads, narrative content |

### Key invariants

1. A report artifact references the snapshot ID it was built from. The snapshot is itself immutable once sealed.
2. Generation is always async. The HTTP layer returns 202 with a job ID; callers poll or subscribe for completion.
3. No report generation path may read mutable live tables directly. All reads go through a sealed snapshot.
4. Published artifacts cannot be updated or deleted through application APIs. Corrections produce new versions with lineage references.

---

## Issue 13-01: Report Definition Model

### Title

Design and implement the report definition schema

### Description

Create the `report_definitions` table and Zod schemas that describe what a report contains: its type, reporting period, benchmark references, section configuration, and client/account scope. Report definitions are the templates from which generation jobs are created. They are tenant-scoped and reusable across periods.

### Scope

- Postgres table `report_definitions` with columns: `id`, `tenant_id`, `name`, `report_type` (enum: `performance`, `holdings`, `activity_statement`, `billing_statement`, `realized_gain_loss`), `period_type` (enum: `monthly`, `quarterly`, `annual`, `custom`), `period_start`, `period_end`, `benchmark_ids` (JSONB array of benchmark references), `sections` (JSONB array describing included sections and ordering), `client_scope` (JSONB -- household IDs, client IDs, or account IDs), `created_by`, `created_at`, `updated_at`, `version` (integer, monotonically increasing)
- Zod request/response schemas in `modules/reports/schemas.ts`
- TypeScript types in `modules/reports/types.ts`
- Repository layer in `modules/reports/repository.ts`
- CRUD endpoints: `POST /api/report-definitions`, `GET /api/report-definitions`, `GET /api/report-definitions/:id`
- Tenant isolation enforced on all queries

### Acceptance Criteria

- [ ] A report definition can be created with type, period, benchmark references, sections, and client scope
- [ ] Report definitions are tenant-scoped; cross-tenant access is rejected
- [ ] Zod validation rejects invalid report types, missing required fields, and malformed section configs
- [ ] Report definitions can be listed with filtering by type and scope
- [ ] A single report definition can be retrieved by ID
- [ ] The `version` field is populated on creation and incremented on update (see Issue 13-11)
- [ ] Database migration is idempotent and includes appropriate indexes (tenant_id, report_type)

### Dependencies

- Epic 1 (tenant context and auth middleware)
- Epic 2 (client/household/account IDs for scope references)

---

## Issue 13-02: Reporting Snapshot Creation

### Title

Implement point-in-time snapshot freezing for report inputs

### Description

Build the snapshot creation pipeline that freezes holdings, performance data, cashflow history, fee/billing data, and benchmark values at a specific point in time. A snapshot is the sole input source for any report generation job. Once sealed, a snapshot is immutable. Snapshots are created as a prerequisite step before report generation begins.

Snapshot data should be stored as structured JSONB in Postgres or as documents in MongoDB (if available). Each snapshot row references the source data version or as-of timestamp used to produce it.

### Scope

- Postgres table `report_snapshots` with columns: `id`, `tenant_id`, `report_definition_id`, `as_of_date`, `as_of_timestamp`, `status` (enum: `building`, `sealed`, `failed`), `holdings_payload` (JSONB or reference to MongoDB doc), `performance_payload` (JSONB or reference), `cashflows_payload` (JSONB or reference), `fees_payload` (JSONB or reference), `benchmark_payload` (JSONB or reference), `account_ids` (JSONB array), `error_detail` (nullable text), `sealed_at` (nullable timestamp), `created_at`
- Service method `createSnapshot(reportDefinitionId, asOfDate)` that: reads current holdings for scoped accounts, reads performance calculations for the period, reads cashflow/transaction history for the period, reads fee/billing data for the period, reads benchmark values, writes all payloads into the snapshot row, transitions status to `sealed`
- If any upstream read fails, snapshot status transitions to `failed` with error detail
- Once status is `sealed`, no further writes to the snapshot row are permitted (enforced at repository level)
- Optional: if MongoDB is available, store large payloads as MongoDB documents and reference them by `_id` from the Postgres row

### Acceptance Criteria

- [ ] A snapshot can be created for a given report definition and as-of date
- [ ] The snapshot captures holdings, performance, cashflows, fees, and benchmarks as frozen data
- [ ] Snapshot status transitions from `building` to `sealed` or `failed`
- [ ] A sealed snapshot cannot be modified through any application code path
- [ ] Snapshot payloads include source metadata: as-of timestamps, account IDs, data version references
- [ ] Failed snapshots include an error detail explaining which data source failed
- [ ] Snapshots are tenant-scoped

### Dependencies

- Issue 13-01 (report definition to determine scope and period)
- Epic 11 (holdings and balance data to freeze)
- Epic 12 (billing/fee data to freeze)

---

## Issue 13-03: Performance Calculation Engine

### Title

Build time-weighted and money-weighted return calculations with benchmark comparison

### Description

Implement the core performance calculation engine that computes returns for accounts and portfolios over configurable periods. This engine is invoked during snapshot creation to produce the frozen performance payload. It must support time-weighted returns (TWR) for client reporting and money-weighted returns (MWR/IRR) for advisor analysis. Benchmark comparison uses frozen benchmark values from the snapshot.

### Scope

- Service module `modules/reports/performance.ts`
- Time-weighted return (TWR) calculation: daily valuation method, handles external cashflows (deposits, withdrawals, dividends, fees) by sub-period chaining
- Money-weighted return (MWR) calculation: internal rate of return using cashflow series and beginning/ending market values
- Benchmark comparison: accepts benchmark return series, computes relative performance (excess return), supports multiple benchmarks per report
- Period aggregation: daily returns compound to monthly, quarterly, annual, inception-to-date, and custom ranges
- Account-level and household-level aggregation (asset-weighted roll-up of account returns)
- Input contract: takes holdings snapshots, cashflow records, and valuation series -- not live database reads
- Output contract: structured performance result object with period returns, cumulative returns, benchmark returns, and excess returns

### Acceptance Criteria

- [ ] TWR calculation correctly handles mid-period cashflows using sub-period chaining
- [ ] MWR/IRR calculation converges for standard cashflow patterns
- [ ] Benchmark comparison produces correct excess returns for each period
- [ ] Returns aggregate correctly across daily, monthly, quarterly, annual, and custom periods
- [ ] Household-level returns are correctly asset-weighted across constituent accounts
- [ ] The engine operates on input data structures, not direct database queries
- [ ] Edge cases handled: zero-balance periods, accounts opened mid-period, accounts with no activity
- [ ] Calculation results include metadata: method used, period boundaries, number of sub-periods

### Dependencies

- Issue 13-02 (provides the frozen input data)
- Epic 11 (valuation and cashflow source data)

---

## Issue 13-04: Holdings Report Generation

### Title

Generate holdings reports from frozen snapshot data

### Description

Build the holdings report generator that takes a sealed snapshot and produces a structured holdings report showing positions, market values, cost basis, unrealized gain/loss, allocation percentages, and sector/asset-class breakdowns. The output is a structured document that can be rendered to PDF or returned as JSON.

### Scope

- Generator function in `modules/reports/generators/holdings.ts`
- Input: sealed snapshot (holdings payload, benchmark payload, account metadata)
- Output sections: position list (security, quantity, price, market value, cost basis, unrealized gain/loss, weight), asset allocation breakdown (by asset class, sector, geography as configured), benchmark allocation comparison (if benchmark included in definition), account-level and household-level summaries
- Structured output format suitable for PDF rendering and JSON API response
- Handles multi-account household reports with per-account detail and consolidated view

### Acceptance Criteria

- [ ] Holdings report is generated exclusively from sealed snapshot data
- [ ] Each position shows security identifier, quantity, price, market value, cost basis, unrealized gain/loss, and portfolio weight
- [ ] Asset allocation breakdown is computed and included
- [ ] Multi-account households produce both per-account and consolidated views
- [ ] Benchmark allocation comparison is included when the report definition specifies benchmarks
- [ ] Generator rejects unsealed snapshots
- [ ] Output structure is documented and stable for downstream PDF rendering

### Dependencies

- Issue 13-02 (sealed snapshot as input)
- Issue 13-01 (report definition for section configuration)

---

## Issue 13-05: Activity/Transaction Statement Generation

### Title

Generate activity and transaction statements from frozen snapshot data

### Description

Build the activity statement generator that produces a period statement of all account activity: trades, deposits, withdrawals, dividends, interest, fees, and corporate actions. Activity statements show the client a complete record of what happened in their account during the reporting period.

### Scope

- Generator function in `modules/reports/generators/activity.ts`
- Input: sealed snapshot (cashflows payload, holdings payload for beginning/ending positions, fee payload)
- Output sections: beginning-of-period summary (positions and value), transaction detail (date, type, description, security, quantity, amount, running balance where applicable), categorized subtotals (buys, sells, deposits, withdrawals, dividends, interest, fees, other), end-of-period summary (positions and value), net change summary
- Transaction types: buy, sell, deposit, withdrawal, dividend, interest, fee_debit, corporate_action, transfer_in, transfer_out, journal
- Chronological ordering with configurable grouping (by date, by type)
- Multi-account and single-account variants

### Acceptance Criteria

- [ ] Activity statement is generated exclusively from sealed snapshot data
- [ ] All transaction types are represented with correct categorization
- [ ] Beginning and ending period summaries are accurate and reconcile with transaction detail
- [ ] Categorized subtotals sum correctly
- [ ] Transactions are chronologically ordered
- [ ] Multi-account households produce per-account statements
- [ ] Generator rejects unsealed snapshots

### Dependencies

- Issue 13-02 (sealed snapshot as input)
- Issue 13-01 (report definition for period and scope)

---

## Issue 13-06: Billing Statement Generation

### Title

Generate billing statements from frozen snapshot data

### Description

Build the billing statement generator that produces a client-facing fee summary for a billing period. The statement shows the fee schedule applied, billable asset values, calculated fees, any adjustments or credits, and the net amount debited. This generator reads from the frozen fees payload in the snapshot, not from live billing tables.

### Scope

- Generator function in `modules/reports/generators/billing.ts`
- Input: sealed snapshot (fees payload containing billing run results, fee schedule metadata, account valuations used for billing)
- Output sections: fee schedule summary (rate, tier breakdowns, billing method), billable asset values by account, calculated fee per account, adjustments and credits (if any), total fee amount, collection method and date, prior period comparison (optional, if included in snapshot)
- Handles household-level billing (aggregated across accounts) and account-level billing
- Handles tiered fee schedules with breakpoint display

### Acceptance Criteria

- [ ] Billing statement is generated exclusively from sealed snapshot data
- [ ] Fee schedule details are clearly presented including tier breakpoints
- [ ] Billable asset values match the frozen snapshot values, not live data
- [ ] Per-account and household-level aggregation are both supported
- [ ] Adjustments and credits are itemized when present
- [ ] Total fee amount is correct and reconciles with line items
- [ ] Generator rejects unsealed snapshots

### Dependencies

- Issue 13-02 (sealed snapshot as input)
- Epic 12 (billing run data frozen into the snapshot)

---

## Issue 13-07: Realized Gain/Loss Tax Views

### Title

Generate realized gain/loss reports from frozen snapshot data

### Description

Build the realized gain/loss report generator for tax reporting purposes. This report shows all closed positions during the period with acquisition date, disposal date, proceeds, cost basis, and gain/loss classified as short-term or long-term. The report is informational and advisory -- it is not a substitute for official tax documents (1099s), but it provides clients and advisors with a working view of tax impact.

### Scope

- Generator function in `modules/reports/generators/realized-gains.ts`
- Input: sealed snapshot (cashflows payload for realized lot disposals, holdings payload for cost basis reference)
- Output sections: realized gain/loss detail (security, acquisition date, disposal date, quantity, proceeds, cost basis, gain/loss, holding period, short-term vs long-term classification), summary by classification (total short-term gains, total short-term losses, total long-term gains, total long-term losses, net realized gain/loss), wash sale flagging (if lot data supports it, flag transactions within 30-day windows -- advisory only)
- Per-account and household-level aggregation
- Disclaimer text indicating this is not an official tax document

### Acceptance Criteria

- [ ] Realized gain/loss report is generated exclusively from sealed snapshot data
- [ ] Each closed lot shows acquisition date, disposal date, proceeds, cost basis, and gain/loss
- [ ] Holding period classification (short-term vs long-term) is correct based on acquisition and disposal dates
- [ ] Summary totals are accurate and reconcile with line-item detail
- [ ] Wash sale flags are advisory and clearly labeled as such
- [ ] Report includes disclaimer that it is not an official tax document
- [ ] Per-account and household-level views are supported
- [ ] Generator rejects unsealed snapshots

### Dependencies

- Issue 13-02 (sealed snapshot as input)
- Epic 9 (execution and lot data that feeds into the snapshot)

---

## Issue 13-08: Async Report Generation Pipeline

### Title

Implement asynchronous report generation with job tracking

### Description

Build the async pipeline that accepts a report generation request, creates a background job, executes snapshot creation and report generation, and allows callers to poll for completion. The HTTP endpoint `POST /api/reports/generate` returns 202 Accepted immediately with a job reference. The job proceeds through snapshot creation, report generation, artifact storage, and publication.

### Scope

- Route: `POST /api/reports/generate` -- accepts `report_definition_id`, optional `as_of_date` (defaults to current date), optional `idempotency_key`
- Response: 202 Accepted with `{ job_id, status, polling_url }`
- Route: `GET /api/reports/jobs/:jobId` -- returns current job status and result references
- Postgres table `report_jobs` with columns: `id`, `tenant_id`, `report_definition_id`, `snapshot_id` (nullable, populated once snapshot is created), `status` (enum: `queued`, `snapshotting`, `generating`, `storing`, `completed`, `failed`), `idempotency_key` (unique per tenant), `error_detail`, `artifact_ids` (JSONB array, populated on completion), `requested_by`, `requested_at`, `started_at`, `completed_at`
- Job execution pipeline (in worker process per api-server spec): create snapshot (Issue 13-02) -> run generators for each section in the report definition -> store artifacts (Issue 13-09) -> mark job completed
- On failure at any stage, job transitions to `failed` with error detail
- Idempotency: duplicate requests with the same idempotency key return the existing job
- Emit domain event `report_generation_completed` or `report_generation_failed` on terminal states

### Acceptance Criteria

- [ ] `POST /api/reports/generate` returns 202 with job ID and polling URL
- [ ] Job progresses through `queued` -> `snapshotting` -> `generating` -> `storing` -> `completed`
- [ ] Failed jobs have status `failed` with a descriptive error detail
- [ ] `GET /api/reports/jobs/:jobId` returns current status and, on completion, artifact references
- [ ] Duplicate requests with the same idempotency key return the existing job, not a new one
- [ ] Job execution runs in the worker process, not in the HTTP request handler
- [ ] Domain events are emitted on completion and failure
- [ ] Jobs are tenant-scoped; cross-tenant access is rejected
- [ ] Permission `report.generate` is required to invoke generation

### Dependencies

- Issue 13-01 (report definition)
- Issue 13-02 (snapshot creation)
- Issues 13-04 through 13-07 (generators)
- Epic 4 (worker process infrastructure, Kafka event emission)

---

## Issue 13-09: Report Artifact Storage

### Title

Store generated report artifacts in object storage with Postgres metadata

### Description

Build the artifact storage layer that takes generated report output (PDFs, structured JSON), writes the binary to object storage, and records metadata in Postgres. Each artifact is associated with a report job, a snapshot, and a report definition. Artifacts are stored with a deterministic, content-addressable or unique key and are retrievable via signed URLs.

### Scope

- Postgres table `report_artifacts` with columns: `id`, `tenant_id`, `report_job_id`, `report_definition_id`, `snapshot_id`, `artifact_type` (enum: `pdf`, `json`, `csv`), `content_type` (MIME type), `storage_key` (object storage path), `storage_bucket`, `file_size_bytes`, `checksum` (SHA-256), `published_at`, `created_at`
- Object storage integration: write artifact bytes with tenant-namespaced key, compute and store checksum
- Signed URL generation for retrieval (time-limited, tenant-scoped)
- PDF rendering: integrate a PDF generation library or service that takes structured report output and produces a PDF artifact
- Support multiple artifact types per report job (e.g., PDF and JSON for the same report)

### Acceptance Criteria

- [ ] Generated reports are written to object storage with a unique, tenant-namespaced key
- [ ] Postgres metadata records the storage key, checksum, content type, and file size
- [ ] Signed URLs can be generated for time-limited artifact download
- [ ] Multiple artifact types (PDF, JSON) can be stored for a single report job
- [ ] Checksum is computed at write time and stored for integrity verification
- [ ] Artifact storage is tenant-isolated at the key/path level
- [ ] Storage failures are surfaced to the job pipeline as errors

### Dependencies

- Epic 5 (object storage integration patterns)
- Issue 13-08 (job pipeline invokes artifact storage)

---

## Issue 13-10: Artifact Immutability Enforcement

### Title

Enforce immutability of published report artifacts

### Description

Once a report artifact is published (i.e., the report job reaches `completed` status and `published_at` is set), neither the artifact binary in object storage nor the metadata row in Postgres may be modified or deleted through any application code path. Corrections are handled by generating a new report version, never by mutating the existing artifact.

### Scope

- Repository-level guards: any `UPDATE` or `DELETE` operation on a `report_artifacts` row where `published_at IS NOT NULL` is rejected with an explicit error
- Object storage policy: artifacts are written to a path/prefix with no application-level delete or overwrite capability; if the storage backend supports object lock or write-once policies, enable them
- API-level enforcement: no endpoint exists to modify or delete a published artifact
- Correction workflow: if a report must be corrected, a new report job is created referencing the original as `supersedes_artifact_id`; the original remains intact
- Add `supersedes_artifact_id` (nullable FK) to `report_artifacts` to track correction lineage
- Audit event emitted if any immutability violation is attempted

### Acceptance Criteria

- [ ] No application code path can update a published artifact's metadata row
- [ ] No application code path can delete a published artifact from object storage
- [ ] Attempting to modify a published artifact returns a clear immutability violation error
- [ ] Corrections produce new artifacts with a `supersedes_artifact_id` reference to the original
- [ ] The original artifact remains accessible and unmodified after a correction is published
- [ ] Audit events are emitted on immutability violation attempts
- [ ] Object storage write-once or object lock policies are documented and enabled where supported

### Dependencies

- Issue 13-09 (artifact storage layer)
- Epic 16 (audit event emission)

---

## Issue 13-11: Versioned Report Definitions

### Title

Implement versioning for report definitions

### Description

Report definitions evolve over time as advisors adjust sections, benchmarks, or scope. Each change to a report definition creates a new version. Report jobs reference the specific definition version used at generation time, so historical reports remain reproducible. The current version is used for new generation requests; prior versions are retained for audit and reproducibility.

### Scope

- Add `version` (integer) column to `report_definitions` (from Issue 13-01), auto-incremented on each update
- Postgres table `report_definition_versions` with columns: `id`, `report_definition_id`, `version`, `tenant_id`, `definition_snapshot` (JSONB -- full copy of the definition at that version), `created_at`, `created_by`
- On every update to a report definition, insert a new row in `report_definition_versions` with the complete prior state
- `report_jobs` table references `definition_version` (integer) in addition to `report_definition_id`
- API: `GET /api/report-definitions/:id/versions` returns version history
- API: `GET /api/report-definitions/:id/versions/:version` returns a specific historical version
- Current version is always the latest; no draft/publish workflow needed for definitions themselves

### Acceptance Criteria

- [ ] Each update to a report definition increments the version number
- [ ] A complete snapshot of the definition is stored in `report_definition_versions` on each change
- [ ] Report jobs reference the specific definition version used for generation
- [ ] Historical versions are retrievable via API
- [ ] Version history is tenant-scoped
- [ ] The definition version used for a report job can be retrieved even after the definition is subsequently modified

### Dependencies

- Issue 13-01 (base report definition model)

---

## Issue 13-12: Report Retrieval APIs

### Title

Implement report and artifact retrieval endpoints

### Description

Build the read APIs that allow advisors and clients to retrieve generated reports, their metadata, and download the published artifacts. These endpoints serve both the advisor portal and the client portal (with different permission scopes).

### Scope

- Route: `GET /api/reports/:id` -- returns report job metadata including status, definition reference, snapshot reference, timestamps, and artifact list
- Route: `GET /api/reports/:id/artifacts` -- returns list of artifacts for a report with metadata (type, size, checksum, created date)
- Route: `GET /api/reports/:id/artifacts/:artifactId/download` -- returns a signed URL or streams the artifact content
- Route: `GET /api/reports` -- list reports with filters: `report_type`, `client_id`, `household_id`, `account_id`, `period_start`, `period_end`, `status`; paginated
- Permission enforcement: `report.read` for advisors, scoped client access for client portal (client can only access their own reports)
- Response includes `as_of_date`, `definition_version`, and `snapshot_id` for traceability

### Acceptance Criteria

- [ ] `GET /api/reports/:id` returns full report metadata including status, timestamps, and artifact references
- [ ] `GET /api/reports/:id/artifacts` returns artifact list with type, size, and checksum
- [ ] `GET /api/reports/:id/artifacts/:artifactId/download` returns a time-limited signed URL for download
- [ ] `GET /api/reports` supports filtering by type, client/household/account scope, period, and status
- [ ] Results are paginated with cursor or offset pagination
- [ ] Tenant isolation is enforced on all queries
- [ ] Advisors with `report.read` permission can access reports for their clients
- [ ] Client portal access is scoped to the authenticated client's own reports only
- [ ] Response payloads include `as_of_date` and `definition_version` for auditability

### Dependencies

- Issue 13-08 (report jobs)
- Issue 13-09 (artifact storage and signed URLs)
- Issue 13-10 (immutability guarantees on retrieved artifacts)
- Epic 1 (auth and permissions)

---

## Issue 13-13: AI Narrative Integration Point

### Title

Add optional AI narrative section to reports using frozen snapshot data

### Description

Provide an integration point where the report generation pipeline can optionally call the Python sidecar to generate narrative commentary (market context, performance explanation, outlook) for inclusion in a report. The sidecar receives only frozen snapshot data -- never live mutable state. If the sidecar is unavailable, the report generates successfully without the narrative section; the narrative is a non-blocking optional enhancement.

Per the sidecar spec, the endpoint is `POST /ai/reports/narrative` and it accepts a snapshot reference with the required context fields (tenant_id, actor_id, request_id).

### Scope

- Integration client in `modules/reports/ai-narrative.ts` that calls the sidecar's `POST /ai/reports/narrative` endpoint
- Request payload: `snapshot_id`, `report_definition_id`, `tenant_id`, `actor_id`, `request_id`, `sections_requested` (array of narrative section types: `performance_summary`, `market_context`, `holdings_commentary`, `outlook`)
- The client sends frozen snapshot data (or a snapshot reference the sidecar can fetch via the platform read API `get_report_snapshot`) -- never live database references
- Response: structured narrative sections with `content`, `as_of`, `confidence`, `citations`, `warnings`
- Graceful degradation: if the sidecar returns an error or times out, the report pipeline continues without the narrative section; the artifact is generated with a placeholder or omitted narrative block
- The narrative content is included in the report artifact alongside quantitative sections
- Timeout: configurable, recommended default 30 seconds

### Acceptance Criteria

- [ ] The generation pipeline can optionally invoke the sidecar for narrative content
- [ ] The sidecar receives only frozen snapshot data or a snapshot reference, never live mutable state
- [ ] If the sidecar is unavailable or times out, report generation completes without the narrative
- [ ] Narrative sections include `as_of`, `confidence`, and `warnings` metadata from the sidecar
- [ ] The narrative call includes required context: `tenant_id`, `actor_id`, `request_id`
- [ ] Timeout is configurable with a sensible default
- [ ] The narrative is embedded into the final report artifact alongside quantitative content
- [ ] An audit log entry records whether narrative generation was attempted, succeeded, or failed

### Dependencies

- Issue 13-02 (sealed snapshot as input to the sidecar)
- Issue 13-08 (pipeline orchestration that calls this integration point)
- Epic 18 / Python sidecar (the `POST /ai/reports/narrative` endpoint)
