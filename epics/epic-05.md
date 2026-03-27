# Epic 5: Document Vault and Records Management

## Goal

Manage uploaded, generated, and signed documents with retention controls, access permissions, and audit logging. Provide the foundational document infrastructure that onboarding cases, account operations, billing, reporting, and the client portal all depend on.

## Context

The platform handles three fundamentally different artifact categories: raw uploads from advisors or clients, signed legal forms produced during onboarding and account operations, and system-generated statements or reports. These must not be conflated. Each has different immutability requirements, retention policies, and access rules.

Document metadata lives in Postgres. Binary artifacts live in S3-compatible object storage. Retrieval is mediated through presigned URLs with permission checks and access logging.

## Dependencies

- Epic 1: Tenant, Identity, and Access Control (tenant scoping, roles, permissions)
- Epic 2: Client, Household, and Account Registry (linkable resources: clients, accounts, households)
- Epic 3: Workflow and Case Management (linkable resources: onboarding cases, transfer cases)

## Reference Specs

- `specs/api-server.md` -- Section 7.6 (DocumentRecord, VaultArtifact, AuditEvent), Section 9.9 (Documents endpoints)
- `specs/data-architecture.md` -- Section 9 (Documents and Vault split model)
- `specs/platform-chassis-v2.md` -- Section 5.8 (Reporting and Documents), Section 6.8 (Document and Records Platform)

---

## Issue 1: Document Upload Intake

### Title

Implement multipart document upload with virus scanning and size limits

### Description

Build the intake pipeline for document uploads. Advisors and clients submit documents (identity proofs, account forms, supporting paperwork) through a multipart upload endpoint. The system must enforce per-tenant and per-file size limits, validate MIME types against an allowlist, stream the upload through a virus scanning step before persisting to object storage, and return a `DocumentRecord` reference on success.

### Scope

- Multipart file upload handler in the documents module route
- Configurable maximum file size per tenant (default: 25 MB)
- MIME type allowlist validation (PDF, PNG, JPG, TIFF, DOCX at minimum)
- Virus/malware scanning integration point (ClamAV or equivalent; design as a pluggable interface so the scanning provider can be swapped)
- Rejection with structured error if scan fails or file exceeds limits
- Temporary staging to local buffer or streaming directly to object storage after scan passes
- Return the created `DocumentRecord` ID and upload status
- Emit a `document.uploaded` domain event

### Acceptance Criteria

- [ ] Multipart POST accepts a single file with metadata fields (document type, description, optional classification)
- [ ] Files exceeding the configured size limit are rejected with a `VALIDATION_ERROR` response before any storage write
- [ ] Files with disallowed MIME types are rejected with a descriptive error
- [ ] Uploaded content is passed through the virus scanning interface; infected files are rejected and the rejection is logged
- [ ] On successful scan, the file is persisted to object storage and a `DocumentRecord` is created in Postgres within the same logical operation
- [ ] The `document.uploaded` event is emitted with tenant ID, actor ID, document ID, and file metadata
- [ ] Upload endpoint requires authentication and the `document.upload` permission

### Dependencies

- Epic 1 (authentication middleware, permission enforcement)
- Issue 2 (object storage integration for persisting the file)
- Issue 3 (DocumentRecord model for persisting metadata)

---

## Issue 2: Object Storage Integration (S3-Compatible)

### Title

Implement S3-compatible object storage client with presigned URL generation

### Description

Create a shared object storage integration layer that the documents module and other modules (reporting, statements) use to store and retrieve binary artifacts. The integration must support any S3-compatible provider (AWS S3, MinIO, R2, etc.), generate presigned URLs for secure time-limited downloads, and organize objects by tenant with a consistent key structure.

### Scope

- Object storage client abstraction in the shared or infrastructure layer
- Configuration for endpoint, region, bucket, access credentials (via environment)
- Key structure: `{tenantId}/{artifactType}/{year}/{month}/{artifactId}/{filename}`
- `putObject` operation: upload a buffer or stream with content type and metadata tags
- `getPresignedUrl` operation: generate a time-limited (default 15 minutes, configurable) signed download URL
- `deleteObject` operation: soft-delete support (move to a delete-pending prefix or apply lifecycle tag) for retention-aware deletion
- `headObject` operation: verify object existence and retrieve storage metadata
- Health check method for startup and readiness probes
- Unit tests using a MinIO container or S3 mock

### Acceptance Criteria

- [ ] Object storage client is configurable via environment variables and injectable through the composition root
- [ ] `putObject` writes a file to the correct tenant-scoped key path and returns the storage key and ETag
- [ ] `getPresignedUrl` returns a URL that expires after the configured TTL; expired URLs return 403 from the storage provider
- [ ] Key structure enforces tenant isolation at the path level
- [ ] `deleteObject` does not perform immediate hard deletion; objects are tagged or moved for retention compliance
- [ ] Client handles transient storage errors with configurable retries
- [ ] Integration test confirms round-trip put, head, presigned-get, and soft-delete against an S3-compatible endpoint

### Dependencies

- Infrastructure: S3-compatible bucket provisioned with appropriate IAM or access policies
- Epic 1 (tenant ID available in request context for key construction)

---

## Issue 3: Document Metadata Model in Postgres

### Title

Define and migrate the DocumentRecord and related metadata tables

### Description

Create the Postgres schema for document metadata. The `document_records` table is the platform's system of record for all document metadata, linking a logical document to its physical artifact in object storage. It carries tenant scoping, document type classification, retention class, uploader identity, and timestamps. This table supports the full document lifecycle: upload, classification, attachment, retrieval, and eventual retention-based disposition.

### Scope

- `document_records` table with columns:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL, foreign key to tenants)
  - `uploaded_by` (UUID, NOT NULL, foreign key to users)
  - `file_name` (text, original file name)
  - `mime_type` (text, NOT NULL)
  - `file_size_bytes` (bigint, NOT NULL)
  - `storage_key` (text, NOT NULL, unique -- the S3 object key)
  - `storage_etag` (text -- returned from object storage on write)
  - `document_type` (text, NOT NULL -- e.g., `identity_proof`, `account_application`, `disclosure`, `supporting_document`)
  - `classification` (text, nullable -- for post-upload AI or manual classification)
  - `retention_class_id` (UUID, nullable, foreign key to retention_classes)
  - `status` (text, NOT NULL -- `pending_scan`, `active`, `quarantined`, `archived`, `deletion_pending`)
  - `description` (text, nullable)
  - `checksum_sha256` (text -- content hash for integrity verification)
  - `metadata` (JSONB, nullable -- extensible key-value metadata)
  - `created_at` (timestamptz, NOT NULL)
  - `updated_at` (timestamptz, NOT NULL)
- Indexes on `tenant_id`, `document_type`, `status`, `uploaded_by`, `created_at`
- Partial index on `status = 'active'` for common query path
- Row-level tenant isolation enforced via query scoping in the repository layer
- Database migration script

### Acceptance Criteria

- [ ] Migration creates the `document_records` table with all specified columns, constraints, and indexes
- [ ] Foreign key to `tenants` table enforces referential integrity
- [ ] `storage_key` has a unique constraint preventing duplicate artifact references
- [ ] Repository layer enforces tenant scoping on all reads and writes (no cross-tenant document access)
- [ ] JSONB `metadata` column allows storing arbitrary key-value pairs without schema migration
- [ ] Migration is idempotent and reversible

### Dependencies

- Epic 1 (tenants and users tables must exist)

---

## Issue 4: Artifact Type Modeling

### Title

Implement distinct artifact type handling for raw uploads, signed forms, and generated statements

### Description

The platform manages three fundamentally different document categories, and the system must treat them as separate concepts with different creation paths, mutability rules, and access patterns:

1. **Raw uploads** -- Files uploaded by advisors or clients during onboarding, account servicing, or support workflows. These are mutable in the sense that they can be reclassified or superseded. Examples: identity documents, bank statements, tax forms.

2. **Signed forms** -- Legally significant documents that have been executed (e-signed or wet-signed). These are immutable once finalized. Examples: account applications, advisory agreements, beneficiary designations, consent forms.

3. **Generated statements** -- System-produced artifacts created by reporting or billing jobs. These are immutable once published. Examples: performance reports, billing invoices, account statements, trade confirmations.

Each artifact type must carry its category in the `document_records` table and enforce the appropriate mutability and lifecycle rules at the service layer.

### Scope

- `artifact_category` enum or constrained text column on `document_records`: `raw_upload`, `signed_form`, `generated_statement`
- Service-layer validation rules:
  - `raw_upload`: may be reclassified, superseded, or archived; standard retention applies
  - `signed_form`: immutable after status transitions to `active`; no reclassification, no content replacement; extended retention
  - `generated_statement`: immutable after publication; regeneration creates a new version record, not an update
- Zod schemas for creation payloads that enforce required fields per category (e.g., signed forms require a `signed_at` timestamp and `signer_ids`)
- TypeScript types that make artifact category a discriminated union in the service layer
- Prevent any storage key overwrite or content mutation for immutable categories

### Acceptance Criteria

- [ ] Every `DocumentRecord` carries an `artifact_category` value from the defined set
- [ ] Attempts to update content or reclassify a `signed_form` with status `active` return a `FORBIDDEN` error
- [ ] Attempts to update content of a `generated_statement` with status `active` return a `FORBIDDEN` error
- [ ] `raw_upload` documents can be reclassified and have their metadata updated
- [ ] Signed form creation requires `signed_at` and at least one signer reference
- [ ] Generated statement creation requires a `source_job_id` or `source_report_id` reference
- [ ] Service-layer and schema-level tests verify immutability enforcement for each category

### Dependencies

- Issue 3 (DocumentRecord table must exist)

---

## Issue 5: Retention Class Management

### Title

Implement retention class definitions and policy enforcement per artifact type

### Description

Different document types have different regulatory and business retention requirements. Identity documents may need to be retained for 6 years after account closure. Signed advisory agreements may require permanent retention. Generated statements may follow a 7-year retention window. The system must model these retention policies explicitly, associate them with document records, and support future automated disposition workflows.

### Scope

- `retention_classes` table:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL, foreign key to tenants)
  - `name` (text, NOT NULL -- e.g., `regulatory_7yr`, `permanent`, `standard_6yr`, `transient_90d`)
  - `description` (text)
  - `retention_days` (integer, nullable -- NULL means permanent retention)
  - `applies_to_categories` (text[] -- which artifact categories this class can be assigned to)
  - `disposition_action` (text -- `archive`, `delete`, `review` -- what happens when retention expires)
  - `is_system_default` (boolean, default false -- platform-provided defaults vs tenant-customized)
  - `created_at` (timestamptz)
  - `updated_at` (timestamptz)
- Unique constraint on `(tenant_id, name)`
- Seed migration with platform default retention classes:
  - `permanent` -- no expiry, applies to signed forms
  - `regulatory_7yr` -- 2555 days, applies to generated statements and signed forms
  - `standard_6yr` -- 2190 days, applies to raw uploads
  - `transient_90d` -- 90 days, applies to raw uploads (temporary supporting docs)
- Service methods:
  - `listRetentionClasses(tenantId)` -- return all classes for a tenant
  - `assignRetentionClass(documentId, retentionClassId)` -- assign or update retention class on a document record
  - `getDocumentsPendingDisposition(tenantId)` -- query documents past their retention window (for future disposition worker)
- Validation: retention class assignment must be compatible with the document's artifact category

### Acceptance Criteria

- [ ] Migration creates `retention_classes` table and seeds default classes
- [ ] Each document record can reference a retention class
- [ ] Assigning a retention class validates that the class's `applies_to_categories` includes the document's `artifact_category`
- [ ] Incompatible retention class assignment returns a `VALIDATION_ERROR`
- [ ] `getDocumentsPendingDisposition` correctly identifies documents whose `created_at + retention_days` has passed and whose `disposition_action` is not yet executed
- [ ] System default retention classes cannot be deleted by tenant administrators
- [ ] Tenant administrators can create custom retention classes scoped to their tenant

### Dependencies

- Issue 3 (DocumentRecord table with `retention_class_id` column)
- Issue 4 (artifact categories must be defined for validation)
- Epic 1 (tenant and permission context)

---

## Issue 6: Version References and Immutability for Signed Artifacts

### Title

Implement version chain tracking and immutability enforcement for signed and generated documents

### Description

Signed forms and generated statements must be immutable once finalized. However, business processes may produce new versions of logically related documents (e.g., an amended advisory agreement, a corrected statement). The system must support version chains that link successive versions of a logical document while preserving every prior version as an immutable, independently retrievable record.

### Scope

- Add columns to `document_records`:
  - `version_number` (integer, NOT NULL, default 1)
  - `version_group_id` (UUID, NOT NULL -- shared across all versions of the same logical document; defaults to the document's own ID for the first version)
  - `supersedes_id` (UUID, nullable, foreign key to `document_records` -- the prior version this document replaces)
  - `is_current_version` (boolean, NOT NULL, default true)
- When a new version is created:
  - The previous version's `is_current_version` is set to `false`
  - The new version's `supersedes_id` points to the previous version
  - The new version's `version_number` increments
  - The new version shares the same `version_group_id`
- Immutability enforcement at the repository and service layer:
  - No UPDATE to `storage_key`, `checksum_sha256`, `file_size_bytes`, or `mime_type` on records with `artifact_category` in (`signed_form`, `generated_statement`) and `status = 'active'`
  - No DELETE of any document record; only status transitions to `archived` or `deletion_pending` are allowed
- Service method: `createNewVersion(originalDocumentId, newFileData)` -- creates a new version in the chain
- Query support: `getVersionHistory(versionGroupId)` -- returns all versions ordered by `version_number`

### Acceptance Criteria

- [ ] Migration adds version tracking columns to `document_records`
- [ ] Creating a new version correctly links to the prior version via `supersedes_id` and shares `version_group_id`
- [ ] Previous version's `is_current_version` is set to `false` within the same transaction
- [ ] Version history query returns all versions of a logical document in order
- [ ] No SQL UPDATE can modify content-bearing columns (`storage_key`, `checksum_sha256`, `file_size_bytes`, `mime_type`) on active signed forms or generated statements
- [ ] No SQL DELETE is issued against `document_records`; the repository layer only performs status transitions
- [ ] Attempting to call `createNewVersion` on a `raw_upload` works normally (raw uploads are versioned but not immutable)
- [ ] Attempting to directly mutate an immutable document's content fields returns a `FORBIDDEN` error

### Dependencies

- Issue 3 (DocumentRecord table)
- Issue 4 (artifact category definitions and immutability rules)

---

## Issue 7: Case and Account Attachment Model

### Title

Implement the document attachment linking model for cases, accounts, clients, and households

### Description

Documents must be linkable to the business resources they relate to: onboarding cases, transfer cases, accounts, clients, and households. A single document may be attached to multiple resources (e.g., an identity proof linked to both a client and an onboarding case). The attachment model is a separate join structure, not an embedded field on the document record, to support many-to-many relationships cleanly.

### Scope

- `document_attachments` table:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL, foreign key to tenants)
  - `document_id` (UUID, NOT NULL, foreign key to document_records)
  - `resource_type` (text, NOT NULL -- `onboarding_case`, `transfer_case`, `account`, `client`, `household`)
  - `resource_id` (UUID, NOT NULL -- the ID of the linked resource)
  - `attached_by` (UUID, NOT NULL, foreign key to users)
  - `attached_at` (timestamptz, NOT NULL)
  - `purpose` (text, nullable -- e.g., `identity_verification`, `funding_proof`, `signed_agreement`, `supporting_evidence`)
  - `notes` (text, nullable)
- Unique constraint on `(document_id, resource_type, resource_id)` to prevent duplicate attachments
- Indexes on `(resource_type, resource_id)` for querying all documents attached to a given resource
- Index on `document_id` for querying all resources a document is attached to
- Service methods:
  - `attachDocument(documentId, resourceType, resourceId, actorId, purpose?)` -- create an attachment
  - `detachDocument(documentId, resourceType, resourceId)` -- remove an attachment (soft delete or hard delete of the link, not the document)
  - `getDocumentsForResource(resourceType, resourceId)` -- list all documents attached to a resource
  - `getAttachmentsForDocument(documentId)` -- list all resources a document is attached to
- Validation: verify that the referenced resource exists and belongs to the same tenant before creating an attachment

### Acceptance Criteria

- [ ] Migration creates `document_attachments` table with all constraints and indexes
- [ ] A document can be attached to multiple resources of different types
- [ ] Duplicate attachment of the same document to the same resource is prevented by the unique constraint
- [ ] Attaching a document to a resource in a different tenant returns a `FORBIDDEN` error
- [ ] Attaching a document to a nonexistent resource returns a `VALIDATION_ERROR`
- [ ] `getDocumentsForResource` returns all active documents for a given resource with their metadata
- [ ] `getAttachmentsForDocument` returns all linked resources for a given document
- [ ] Detaching a document removes the link but does not delete or modify the document record itself
- [ ] Attachment creation requires the `document.attach` permission

### Dependencies

- Issue 3 (DocumentRecord table)
- Epic 2 (client, account, household tables for resource validation)
- Epic 3 (onboarding_cases, transfer_cases tables for resource validation)
- Epic 1 (permission enforcement)

---

## Issue 8: Secure Retrieval with Permission Checks and Access Logging

### Title

Implement permissioned document retrieval with presigned URL generation and access logging

### Description

Document retrieval must enforce tenant isolation, role-based access rules, and artifact-category-specific sensitivity checks before generating a presigned download URL. Every successful retrieval must be logged for audit purposes. Sensitive documents (signed forms with PII, identity proofs) require the `document.read_sensitive` permission. Standard documents require the `document.read` permission.

### Scope

- Retrieval flow in the documents service:
  1. Resolve document by ID, scoped to the requesting tenant
  2. Verify the actor has the required permission (`document.read` or `document.read_sensitive` depending on document type and classification)
  3. If the document is attached to resources, optionally verify the actor has access to at least one linked resource (e.g., advisor has the client relationship)
  4. Generate a presigned URL via the object storage client (Issue 2)
  5. Log the access event (Issue 9)
  6. Return the presigned URL, document metadata, and URL expiry timestamp
- Sensitivity classification rules:
  - Documents with `document_type` in (`identity_proof`, `tax_document`, `financial_statement`) require `document.read_sensitive`
  - Signed forms always require `document.read_sensitive`
  - Generated statements require `document.read`
  - Raw uploads default to `document.read` unless their classification marks them sensitive
- Deny retrieval for documents in `quarantined` or `deletion_pending` status
- Redis-based short-lived cache (optional) for repeated presigned URL requests within a narrow window to avoid excessive S3 signing calls

### Acceptance Criteria

- [ ] Retrieval for a document in another tenant returns `NOT_FOUND` (not `FORBIDDEN`, to avoid information leakage)
- [ ] Retrieval without the required permission returns `FORBIDDEN`
- [ ] Retrieval of a quarantined document returns `FORBIDDEN` with an appropriate error code
- [ ] Successful retrieval returns a presigned URL, document metadata, content type, and expiry timestamp
- [ ] Presigned URL is time-limited and becomes invalid after expiry
- [ ] Every successful retrieval produces an access log entry (see Issue 9)
- [ ] Sensitive documents are only accessible to actors with `document.read_sensitive`
- [ ] Retrieval of documents in `pending_scan` status returns a `DOCUMENT_NOT_READY` error

### Dependencies

- Issue 2 (presigned URL generation)
- Issue 3 (DocumentRecord lookup)
- Issue 9 (access audit trail)
- Epic 1 (permission model, `document.read`, `document.read_sensitive` permissions)

---

## Issue 9: Document Access Audit Trail

### Title

Implement append-only audit logging for all document access and mutations

### Description

Every document operation -- upload, retrieval, classification, attachment, detachment, version creation, and retention disposition -- must produce an append-only audit record. This is a regulatory and compliance requirement. The audit trail must be queryable by tenant, actor, document, resource, and time range. It supports internal investigations, compliance reviews, and client data access requests.

### Scope

- `document_access_log` table:
  - `id` (UUID, primary key)
  - `tenant_id` (UUID, NOT NULL)
  - `document_id` (UUID, NOT NULL, foreign key to document_records)
  - `actor_id` (UUID, NOT NULL -- the user who performed the action)
  - `action` (text, NOT NULL -- `uploaded`, `retrieved`, `classified`, `attached`, `detached`, `version_created`, `archived`, `disposition_initiated`)
  - `resource_type` (text, nullable -- if the action involves an attachment target)
  - `resource_id` (UUID, nullable)
  - `ip_address` (inet, nullable)
  - `user_agent` (text, nullable)
  - `details` (JSONB, nullable -- action-specific context, e.g., classification change from/to, retention class assigned)
  - `created_at` (timestamptz, NOT NULL, default now())
- Table is append-only: no UPDATE or DELETE operations permitted at the application layer
- Indexes: `(tenant_id, created_at)`, `(tenant_id, document_id)`, `(tenant_id, actor_id)`, `(tenant_id, resource_type, resource_id)`
- Service helper: `logDocumentAccess(params)` -- called by the documents service after each operation
- Query methods:
  - `getAccessLogForDocument(tenantId, documentId, pagination)` -- audit history for a single document
  - `getAccessLogForActor(tenantId, actorId, dateRange, pagination)` -- all document actions by a specific user
  - `getAccessLogForResource(tenantId, resourceType, resourceId, pagination)` -- all document actions related to a business resource
- Emit `document.access_logged` event for downstream consumption (e.g., by the audit and compliance module in Epic 16)

### Acceptance Criteria

- [ ] Migration creates the `document_access_log` table with all columns and indexes
- [ ] Every document upload, retrieval, classification, attachment, detachment, and version creation writes a log entry
- [ ] The log table has no UPDATE or DELETE operations in the repository layer
- [ ] Log entries include the actor's IP address and user agent when available from the request context
- [ ] Query methods support pagination and date-range filtering
- [ ] Log entries for retrieval of sensitive documents include the permission used for access
- [ ] `document.access_logged` events are emitted for downstream audit integration
- [ ] Log queries are tenant-scoped; no cross-tenant log access is possible

### Dependencies

- Issue 3 (DocumentRecord table for foreign key)
- Epic 1 (actor context for logging, tenant scoping)

---

## Issue 10: Vault Artifact Endpoints

### Title

Implement the document vault HTTP API endpoints

### Description

Expose the document vault functionality through the Hono route layer. These endpoints compose the upload intake, metadata model, attachment logic, retrieval flow, and audit logging into a coherent API surface. All endpoints follow the platform's standard request pipeline: request ID, tenant resolution, authentication, permission enforcement, Zod validation, handler, and audit emission.

### Scope

#### `POST /api/documents`

Upload a new document.

- Multipart request with file and metadata fields
- Required fields: `file`, `documentType`, `artifactCategory`
- Optional fields: `description`, `classification`, `retentionClassId`, `metadata`
- Additional required fields for `signed_form`: `signedAt`, `signerIds`
- Additional required fields for `generated_statement`: `sourceJobId` or `sourceReportId`
- Returns: `201 Created` with the `DocumentRecord` (excluding presigned URL; retrieval is separate)
- Permissions: `document.upload`

#### `GET /api/documents/:id`

Retrieve document metadata and a presigned download URL.

- Returns: document metadata, presigned URL, URL expiry, version info, attachment summary
- Returns `404` for documents outside the actor's tenant
- Returns `403` if the actor lacks the required permission for the document's sensitivity level
- Returns `409` with `DOCUMENT_NOT_READY` if the document is still in `pending_scan`
- Permissions: `document.read` or `document.read_sensitive` depending on document type

#### `POST /api/documents/:id/attach`

Attach a document to a business resource.

- Request body: `{ resourceType, resourceId, purpose?, notes? }`
- Zod validation for `resourceType` against allowed values
- Returns: `201 Created` with the attachment record
- Returns `409` if the attachment already exists
- Permissions: `document.attach`

#### `GET /api/documents/:id/attachments`

List all resources a document is attached to.

- Returns: array of attachment records with resource type, resource ID, purpose, and attached-at timestamp
- Permissions: `document.read`

#### `GET /api/vault/artifacts/:id`

Direct artifact retrieval endpoint for the client-facing vault experience.

- Simpler response shape optimized for the client portal: file name, content type, presigned URL, expiry
- Only returns documents that are attached to resources the requesting client has access to
- Enforces client-portal-specific permission rules (clients can only see their own documents)
- Permissions: `vault.read` (client-scoped permission)

#### Supporting query endpoints

- `GET /api/documents?resourceType=X&resourceId=Y` -- list documents for a resource
- `GET /api/documents?documentType=X&status=active` -- list documents by type and status
- Pagination via cursor-based approach
- Filtering by `artifactCategory`, `documentType`, `status`, `uploadedBy`, `createdAfter`, `createdBefore`

### Acceptance Criteria

- [ ] `POST /api/documents` accepts multipart upload, validates inputs with Zod, and returns the created document record
- [ ] `POST /api/documents` rejects files that fail size, MIME type, or virus scan checks with appropriate error codes
- [ ] `GET /api/documents/:id` returns metadata and a presigned URL for authorized actors
- [ ] `GET /api/documents/:id` returns `404` for cross-tenant requests and `403` for insufficient permissions
- [ ] `POST /api/documents/:id/attach` creates an attachment link and returns `201`
- [ ] `POST /api/documents/:id/attach` returns `409` for duplicate attachments
- [ ] `GET /api/vault/artifacts/:id` enforces client-scoped access and returns a simplified response
- [ ] All endpoints pass through the standard middleware pipeline (request ID, tenant, auth, permissions)
- [ ] All endpoints validate request bodies and query parameters with Zod schemas
- [ ] All mutating endpoints log to the document access audit trail
- [ ] List endpoints support cursor-based pagination and filtering
- [ ] Error responses use the platform's standard error envelope with machine-readable codes (`VALIDATION_ERROR`, `FORBIDDEN`, `DOCUMENT_NOT_READY`, `IDEMPOTENCY_CONFLICT`)
- [ ] Route definitions live in `modules/documents/routes.ts`; business logic lives in `modules/documents/service.ts`; database access lives in `modules/documents/repository.ts`

### Dependencies

- Issue 1 (upload intake logic)
- Issue 2 (object storage and presigned URLs)
- Issue 3 (DocumentRecord model and repository)
- Issue 4 (artifact category validation)
- Issue 5 (retention class assignment on upload)
- Issue 6 (version info in retrieval response)
- Issue 7 (attachment model)
- Issue 8 (permission-checked retrieval with presigned URLs)
- Issue 9 (audit logging on every operation)
- Epic 1 (full middleware pipeline, permissions)
