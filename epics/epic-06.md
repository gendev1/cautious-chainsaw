# Epic 6: Onboarding and Account Opening

## Goal

Build the full digital onboarding and account opening workflow on top of the case engine from Epic 3. Onboarding is not a linear form submission -- it is a case-based workflow envelope with explicit advisor initiation, client data collection, disclosure management, internal review, exception handling, and activation handoff. The implementation must support single-account and batch account opening, attach documents and legal party data to the case, and produce a durable, auditable record of every state transition.

## Dependencies

- Epic 1: Tenant, Identity, and Access Control (authentication, authorization, role enforcement)
- Epic 2: Client, Household, and Account Registry (households, clients, legal parties, account registrations, beneficiaries, trusted contacts)
- Epic 3: Workflow and Case Management (case lifecycle, status machine, notes, exception states, approval requests, workflow history)
- Epic 4: External Service Integration Framework (Kafka event publishing, idempotency, correlation IDs)
- Epic 5: Document Vault and Records Management (upload intake, document metadata, case attachment model, signed artifact storage)

## Onboarding Case Status Lifecycle

```
draft
  --> submitted
  --> pending_client_action

pending_client_action
  --> submitted

submitted
  --> pending_internal_review
  --> exception

pending_internal_review
  --> approved
  --> rejected
  --> exception

exception
  --> pending_internal_review
  --> rejected

approved
  --> activated

rejected
  (terminal)

activated
  (terminal)
```

## API Surface

| Method | Path | Purpose |
|--------|------|---------|
| POST   | `/api/onboarding-cases` | Create a new onboarding case |
| GET    | `/api/onboarding-cases/:id` | Retrieve case with full state |
| POST   | `/api/onboarding-cases/:id/submit` | Submit case for review |
| POST   | `/api/onboarding-cases/:id/request-client-action` | Move case to pending_client_action |
| POST   | `/api/onboarding-cases/:id/approve` | Approve the case |
| POST   | `/api/onboarding-cases/:id/reject` | Reject the case |
| POST   | `/api/onboarding-cases/:id/add-note` | Append an audit note |
| GET    | `/api/onboarding-cases` | List/search cases with filters |
| GET    | `/api/onboarding-cases/:id/history` | Retrieve status transition history |

---

## Issues

### Issue 6.1: Advisor-Initiated Onboarding Case Creation

**Title:** Implement POST /api/onboarding-cases for advisor-initiated case creation

**Description:**
An advisor creates a new onboarding case to begin the account opening process for a client or household. The case is created in `draft` status and serves as the envelope for all onboarding sub-processes: legal party capture, disclosures, document collection, beneficiary designation, and account registration selection. The endpoint must accept an initial payload that identifies the target household (existing or to-be-created), the advisor, and one or more requested account types. An idempotency key must be accepted to prevent duplicate case creation.

**Scope:**
- Hono route at `POST /api/onboarding-cases` with Zod request validation
- OnboardingCase record creation in Postgres with status `draft`, tenant scoping, and timestamps
- Accept `household_id` (existing) or inline household creation intent
- Accept one or more `account_registration` entries with account type, registration type, and titling
- Accept optional initial client references (existing `client_person_id` or `client_entity_id`)
- Idempotency key header support to prevent duplicate cases
- Permission guard requiring `account.open` capability
- Emit `onboarding_case.created` domain event via Kafka
- Emit audit event recording the creating actor, tenant, and case ID
- Return `201 Created` with case ID, status, and polling URL

**Acceptance Criteria:**
- An advisor with `account.open` permission can create a case that persists in `draft` status
- The case is tenant-scoped and linked to the authenticated advisor
- Duplicate requests with the same idempotency key return the existing case without side effects
- Requests without required fields are rejected with `VALIDATION_ERROR`
- Unauthorized actors receive `FORBIDDEN`
- A Kafka domain event is published on successful creation
- An audit event is persisted recording actor, action, and resource
- The response includes case ID, current status, created timestamp, and a GET polling URL

**Dependencies:**
- Epic 1: auth middleware, permission guards
- Epic 2: household and client registry, account registration types
- Epic 3: case record schema, status enum, workflow history table
- Epic 4: Kafka producer, idempotency key middleware

---

### Issue 6.2: Client Action Collection Flow

**Title:** Implement pending_client_action status and client-facing data capture

**Description:**
After an advisor creates a draft case and populates initial data, the advisor may request that the client complete remaining information. The `POST /api/onboarding-cases/:id/request-client-action` endpoint transitions the case from `draft` to `pending_client_action`, optionally specifying which data sections the client must complete (e.g., personal information, employment, financial profile, beneficiaries). The client then interacts with a scoped, client-facing API to supply the requested data. When the client has completed all required sections, the case can be submitted.

**Scope:**
- Hono route at `POST /api/onboarding-cases/:id/request-client-action`
- State transition guard: only valid from `draft` status
- Accept a list of `required_sections` describing what the client must provide
- Persist required sections and completion status per section on the case
- Client-facing data capture endpoints scoped to the case:
  - `PUT /api/onboarding-cases/:id/client-data/personal` (personal info, SSN/TIN, address, DOB)
  - `PUT /api/onboarding-cases/:id/client-data/employment` (employment status, employer, occupation)
  - `PUT /api/onboarding-cases/:id/client-data/financial-profile` (income, net worth, investment objectives, risk tolerance)
  - `PUT /api/onboarding-cases/:id/client-data/regulatory` (affiliations, control persons, political exposure)
- Each client-data endpoint validates input via Zod and marks its section as complete
- Client-facing endpoints enforce client-actor authentication (not advisor role)
- Emit `onboarding_case.client_action_requested` event
- Emit `onboarding_case.client_data_submitted` event per section completion

**Acceptance Criteria:**
- An advisor can transition a `draft` case to `pending_client_action` with a list of required sections
- Transition from any status other than `draft` returns `INVALID_WORKFLOW_STATE`
- A client with a valid session scoped to the case can submit data for each required section
- Each section submission is independently validated and persisted
- Section completion is tracked; all required sections must be complete before the case can be submitted
- Client-data endpoints reject advisor-role tokens (client-only access)
- Domain events are emitted for the action request and each section completion
- Audit events record both the advisor action request and each client data submission

**Dependencies:**
- Issue 6.1: case must exist in `draft`
- Epic 1: client authentication and session model, actor type distinction
- Epic 3: status transition enforcement, workflow history

---

### Issue 6.3: Disclosures and Consent Management

**Title:** Implement disclosure presentation, acceptance tracking, and consent records

**Description:**
Onboarding requires the client to receive and acknowledge specific disclosures before the case can be submitted. Disclosures are versioned documents (e.g., advisory agreement, privacy policy, Form CRS, account agreement) that must be presented, accepted, and recorded with timestamps and version identifiers. Consent records are append-only and must survive case state transitions. This issue covers both the disclosure configuration and the acceptance tracking within an onboarding case.

**Scope:**
- Disclosure definition table in Postgres: `id`, `tenant_id`, `disclosure_type`, `version`, `title`, `document_id` (reference to document vault), `effective_date`, `is_active`
- Case disclosure requirement: each case type maps to a set of required disclosures based on account registration types and tenant configuration
- Consent record table: `id`, `case_id`, `disclosure_id`, `disclosure_version`, `accepted_by` (actor ID), `accepted_at`, `ip_address`, `user_agent`
- Endpoint: `POST /api/onboarding-cases/:id/disclosures/:disclosureId/accept`
- Endpoint: `GET /api/onboarding-cases/:id/disclosures` (list required disclosures with acceptance status)
- Consent records are immutable once created -- no update or delete
- Submission gate: case cannot transition to `submitted` unless all required disclosures are accepted
- Emit `onboarding_case.disclosure_accepted` event per acceptance

**Acceptance Criteria:**
- The system resolves which disclosures are required for a case based on account types and tenant configuration
- `GET .../disclosures` returns each required disclosure with its acceptance status (pending or accepted with timestamp)
- A client or advisor can accept a disclosure, creating an immutable consent record
- Duplicate acceptance of the same disclosure version is idempotent (returns existing record)
- Attempting to submit a case with unaccepted disclosures returns a validation error listing the missing disclosures
- Consent records include IP address, user agent, actor ID, and timestamp
- Consent records cannot be modified or deleted after creation
- Domain and audit events are emitted for each acceptance

**Dependencies:**
- Issue 6.1: case must exist
- Epic 5: document vault for disclosure document storage and retrieval
- Epic 3: submission gate integration

---

### Issue 6.4: Legal Party Capture Within Onboarding

**Title:** Implement legal party creation and linking within onboarding cases

**Description:**
An onboarding case must capture legal party information for account holders, which may be persons (individuals) or entities (trusts, LLCs, corporations). Legal party data captured during onboarding is written to the client registry (Epic 2) and linked to the case. The case may reference existing clients or create new client records. For joint accounts, multiple person parties are linked. For entity accounts, the entity record and its authorized signers are captured. This issue handles the creation, validation, and registry linking of legal parties within the onboarding context.

**Scope:**
- Endpoints within the onboarding case context:
  - `POST /api/onboarding-cases/:id/parties` (add a legal party to the case)
  - `GET /api/onboarding-cases/:id/parties` (list parties linked to the case)
  - `DELETE /api/onboarding-cases/:id/parties/:partyId` (remove a party link, only in `draft` or `pending_client_action`)
- Party payload supports two types:
  - `person`: first name, last name, DOB, SSN/TIN (encrypted at rest), address, citizenship, identification document references
  - `entity`: entity name, entity type (trust, LLC, corporation, partnership), TIN, formation state, formation date, governing document references
- If `existing_client_id` is provided, validate it exists in the client registry and link it
- If no existing client, create a new `ClientPerson` or `ClientEntity` record in the registry and link to the case
- Case-party join table: `case_id`, `party_id`, `party_type`, `role` (primary_holder, joint_holder, entity_owner, authorized_signer)
- Zod validation for all required fields per party type and account registration type
- SSN/TIN must be encrypted at rest and masked in API responses (last 4 digits only)
- Emit `onboarding_case.party_added` and `onboarding_case.party_removed` events

**Acceptance Criteria:**
- An advisor can add a person or entity party to a draft or pending_client_action case
- A client can add party data via client-facing endpoints in pending_client_action status
- Existing client registry records can be linked by ID without duplication
- New party data creates a corresponding client registry record
- Party removal is only allowed in `draft` or `pending_client_action` status
- SSN/TIN is encrypted at rest and masked in all API responses
- Each party has an explicit role (primary_holder, joint_holder, authorized_signer, etc.)
- Validation enforces required fields based on party type and account registration type
- Domain and audit events are emitted for additions and removals

**Dependencies:**
- Issue 6.1: case must exist
- Epic 2: ClientPerson, ClientEntity records, client registry repository
- Epic 1: encryption at rest for PII fields

---

### Issue 6.5: Beneficiary and Trusted Contact Workflows Within Onboarding

**Title:** Implement beneficiary designation and trusted contact capture within onboarding cases

**Description:**
Certain account types (IRA, retirement, TOD) require beneficiary designations. FINRA Rule 4512 requires firms to make reasonable efforts to obtain trusted contact information. Both are captured within the onboarding case context, persisted to the client/account registry, and tracked for completeness before submission. Beneficiaries have types (primary, contingent), allocation percentages, and relationship data. Trusted contacts have limited scope (name, relationship, contact information) and do not have account authority.

**Scope:**
- Beneficiary endpoints within onboarding:
  - `POST /api/onboarding-cases/:id/beneficiaries` (add beneficiary)
  - `GET /api/onboarding-cases/:id/beneficiaries` (list beneficiaries)
  - `PUT /api/onboarding-cases/:id/beneficiaries/:beneficiaryId` (update beneficiary)
  - `DELETE /api/onboarding-cases/:id/beneficiaries/:beneficiaryId` (remove, only in draft/pending_client_action)
- Beneficiary fields: full name, DOB, SSN/TIN (optional, encrypted), relationship, type (primary/contingent), allocation percentage, per stirpes flag
- Validation: primary beneficiary allocations must sum to 100%; contingent allocations must sum to 100% if any contingent beneficiaries exist
- Trusted contact endpoints within onboarding:
  - `POST /api/onboarding-cases/:id/trusted-contacts` (add trusted contact)
  - `GET /api/onboarding-cases/:id/trusted-contacts` (list trusted contacts)
  - `PUT /api/onboarding-cases/:id/trusted-contacts/:contactId` (update)
  - `DELETE /api/onboarding-cases/:id/trusted-contacts/:contactId` (remove, only in draft/pending_client_action)
- Trusted contact fields: full name, relationship, phone, email, mailing address
- Submission gate: if account type requires beneficiaries, they must be present and valid before submission
- On case approval, beneficiary and trusted contact records are persisted to the account-level registry (Epic 2)
- Emit domain events for additions, updates, and removals

**Acceptance Criteria:**
- Beneficiaries can be added, updated, and removed while the case is in `draft` or `pending_client_action`
- Primary beneficiary allocation percentages are validated to sum to 100%
- Contingent beneficiary allocations are validated independently
- Trusted contacts can be added and managed within the case
- Accounts that require beneficiaries (IRA, retirement, TOD) cannot be submitted without valid beneficiary designations
- On case approval, beneficiary and trusted contact data is written to the account-level registry
- SSN/TIN for beneficiaries is encrypted at rest and masked in responses
- Domain and audit events are emitted for all mutations

**Dependencies:**
- Issue 6.1: case must exist
- Issue 6.4: legal parties must be captured (beneficiaries relate to account holders)
- Epic 2: Beneficiary and TrustedContact registry models

---

### Issue 6.6: Document Collection and Attachment to Case

**Title:** Implement document upload, classification, and attachment within onboarding cases

**Description:**
Onboarding may require supporting documents such as government-issued ID, trust agreements, entity formation documents, proof of address, or other compliance artifacts. Documents are uploaded through the document vault (Epic 5), classified by type, and attached to the onboarding case. The case tracks which document types are required (based on account type and party type) and which have been satisfied. Documents attached to a case become part of the permanent onboarding record.

**Scope:**
- Endpoint: `POST /api/onboarding-cases/:id/documents` (upload and attach a document to the case)
- Endpoint: `GET /api/onboarding-cases/:id/documents` (list documents attached to the case with metadata)
- Endpoint: `DELETE /api/onboarding-cases/:id/documents/:documentId` (detach, only in draft/pending_client_action; the vault record is not deleted)
- Document classification types relevant to onboarding: `government_id`, `trust_agreement`, `entity_formation`, `proof_of_address`, `power_of_attorney`, `death_certificate`, `court_order`, `other`
- Required document rules: tenant-configurable matrix mapping account types and party types to required document classifications
- Document completeness check integrated into the submission gate
- Upload delegates to Epic 5 document vault; this issue handles the case-document join and completeness tracking
- Emit `onboarding_case.document_attached` and `onboarding_case.document_detached` events

**Acceptance Criteria:**
- An advisor or client can upload and attach a document to a case in `draft` or `pending_client_action` status
- Each attached document has a classification type
- The system tracks which required document types are satisfied per case based on tenant configuration
- `GET .../documents` returns all attached documents with metadata and classification
- Documents can be detached in draft/pending_client_action but the underlying vault record is preserved
- The submission gate blocks submission if required documents are missing
- Domain and audit events are emitted for attach and detach actions

**Dependencies:**
- Issue 6.1: case must exist
- Epic 5: document vault upload, metadata, and retrieval
- Issue 6.4: party types drive document requirements

---

### Issue 6.7: Internal Review and Approval Flow

**Title:** Implement pending_internal_review, approve, and reject transitions for onboarding cases

**Description:**
After a case is submitted, it enters `pending_internal_review` where an operations or compliance user reviews the case for completeness, regulatory compliance, and data accuracy. The reviewer may approve or reject the case. Approval transitions the case to `approved`. Rejection transitions it to `rejected` (terminal) with a required reason. Both actions require explicit permission and produce durable audit records. The review flow must support reviewer assignment, review notes, and multi-reviewer visibility.

**Scope:**
- State transition: `submitted` --> `pending_internal_review` (automatic on submit)
- Hono route: `POST /api/onboarding-cases/:id/approve`
  - Permission guard: `onboarding.approve` capability
  - Required payload: reviewer notes (optional)
  - Transition: `pending_internal_review` --> `approved`
- Hono route: `POST /api/onboarding-cases/:id/reject`
  - Permission guard: `onboarding.approve` capability
  - Required payload: rejection reason (mandatory)
  - Transition: `pending_internal_review` --> `rejected`
- Review assignment: optional `assigned_reviewer_id` field on the case, settable by operations users
- Review checklist: configurable per-tenant checklist items that the reviewer confirms (e.g., "identity verified", "disclosures complete", "documents reviewed")
- All transitions recorded in workflow history with actor, timestamp, and notes
- Emit `onboarding_case.approved` or `onboarding_case.rejected` domain events
- Emit audit events for both approve and reject actions

**Acceptance Criteria:**
- Only users with `onboarding.approve` permission can approve or reject a case
- Approval is only valid from `pending_internal_review` status; other statuses return `INVALID_WORKFLOW_STATE`
- Rejection requires a reason; rejection without a reason returns `VALIDATION_ERROR`
- Both actions create immutable workflow history entries with actor ID, timestamp, and notes
- Kafka domain events are published for approval and rejection
- Audit events are persisted for both actions
- An optional reviewer assignment can be set on the case
- Review checklist items (if configured) are tracked per case

**Dependencies:**
- Issue 6.1: case creation
- Issue 6.2, 6.3, 6.4, 6.5, 6.6: all submission gates must pass before review begins
- Epic 1: `onboarding.approve` permission
- Epic 3: workflow history, status transitions

---

### Issue 6.8: Exception Handling Within Onboarding

**Title:** Implement durable exception states and resolution workflow for onboarding cases

**Description:**
Onboarding cases may encounter exceptions at any review stage -- missing information, failed identity verification, compliance flags, or upstream rejections. Exception states must be durable (not transient error responses) and require explicit resolution. A case in `exception` status must capture the exception reason, allow notes and document attachments, and provide a path back to `pending_internal_review` once resolved. Exceptions are not silent failures; they appear in operational queues and dashboards.

**Scope:**
- State transitions into `exception`:
  - From `submitted` (e.g., automated pre-check failure)
  - From `pending_internal_review` (e.g., reviewer flags an issue)
- Endpoint: `POST /api/onboarding-cases/:id/flag-exception`
  - Required payload: exception reason, exception category (e.g., `identity_verification`, `missing_document`, `compliance_flag`, `data_mismatch`, `upstream_rejection`)
  - Transition: `submitted` or `pending_internal_review` --> `exception`
  - Permission: `onboarding.approve` or `operations` role
- Resolution endpoint: `POST /api/onboarding-cases/:id/resolve-exception`
  - Required payload: resolution notes
  - Transition: `exception` --> `pending_internal_review`
  - Permission: `onboarding.approve` or `operations` role
- Exception record table: `id`, `case_id`, `category`, `reason`, `flagged_by`, `flagged_at`, `resolved_by`, `resolved_at`, `resolution_notes`
- Multiple exceptions can exist on a single case (append-only exception history)
- Cases in `exception` status appear in operational task queues (Epic 3)
- Emit `onboarding_case.exception_flagged` and `onboarding_case.exception_resolved` events
- Exception-to-rejected transition: `POST .../reject` is also valid from `exception` status

**Acceptance Criteria:**
- A reviewer or operations user can flag an exception with a category and reason
- Exception transitions are only valid from `submitted` or `pending_internal_review`
- An exception creates a durable, queryable exception record on the case
- The case remains in `exception` until explicitly resolved or rejected
- Resolution transitions the case back to `pending_internal_review` for re-review
- Multiple exceptions on a single case are tracked independently with full history
- Exception cases appear in operational queues
- Domain and audit events are emitted for flagging and resolution
- Rejection is valid from `exception` status as a terminal resolution

**Dependencies:**
- Issue 6.7: review flow must exist
- Epic 3: operational task queues, exception state support
- Epic 1: `onboarding.approve` or `operations` role permission

---

### Issue 6.9: Account Activation Handoff

**Title:** Implement approved-to-activated transition and downstream provisioning handoff

**Description:**
After a case is approved, account activation is a separate step that represents downstream provisioning -- opening the account at the custodian/clearing layer, assigning account numbers, and confirming operational readiness. The transition from `approved` to `activated` may be triggered by an internal process (after downstream confirmation), an operations user (manual activation), or an async event from an external system. Activation is the point at which accounts become operational and visible in the client and advisor experience. This step must be explicit and auditable, not an automatic side effect of approval.

**Scope:**
- Endpoint: `POST /api/onboarding-cases/:id/activate`
  - Permission: `onboarding.activate` or `operations` role
  - Transition: `approved` --> `activated`
  - Accepts optional external account identifiers (custodian account numbers, clearing references)
- On activation:
  - Update each `AccountRegistration` linked to the case to `active` status in the account registry
  - Persist external account identifiers on the account records
  - Link beneficiaries and trusted contacts from the case to the activated account records
  - Emit `onboarding_case.activated` domain event
  - Emit `account.activated` domain event per account
  - Emit audit events
- Kafka consumer: optionally listen for external activation confirmation events and trigger the transition automatically
- Activation must be idempotent (re-activation of an already activated case returns the existing state)
- Post-activation: the case is terminal and immutable except for note additions

**Acceptance Criteria:**
- Only `approved` cases can be activated; other statuses return `INVALID_WORKFLOW_STATE`
- Activation requires `onboarding.activate` permission
- Each account registration linked to the case transitions to `active` in the registry
- External account identifiers are persisted if provided
- Beneficiary and trusted contact data is written to the account-level registry
- Domain events are published for the case and each activated account
- Audit events record the activating actor, timestamp, and any external references
- Duplicate activation requests are idempotent
- After activation, the case status is terminal (only `add-note` is permitted)

**Dependencies:**
- Issue 6.7: case must be in `approved` status
- Epic 2: account registry status updates, beneficiary and trusted contact persistence
- Epic 4: Kafka consumer for external activation events (optional)

---

### Issue 6.10: Batch Account Opening

**Title:** Support multiple account registrations within a single onboarding case

**Description:**
A common onboarding scenario involves opening multiple accounts for the same client or household in a single workflow -- for example, an individual brokerage account, a Traditional IRA, and a Roth IRA. The onboarding case must support multiple account registrations, each with its own account type, registration type, titling, beneficiary requirements, and document requirements. All accounts in a batch case share the same legal party data and disclosures but may have independent beneficiary designations and document requirements. The case is approved or rejected as a unit, and all accounts activate together.

**Scope:**
- Extend the case creation payload (Issue 6.1) to accept an array of account registrations
- Each account registration within the case:
  - Has its own `account_type`, `registration_type`, and titling
  - May have independent beneficiary requirements (e.g., IRA requires beneficiaries, brokerage does not)
  - May have independent document requirements
- Endpoints for managing account registrations within a case:
  - `POST /api/onboarding-cases/:id/accounts` (add an account registration to the case)
  - `GET /api/onboarding-cases/:id/accounts` (list account registrations in the case)
  - `DELETE /api/onboarding-cases/:id/accounts/:accountRegId` (remove, only in draft/pending_client_action)
- Submission gate validates all accounts in the case: each must have required parties, beneficiaries (if applicable), and documents
- Approval and activation apply to the entire case and all its accounts
- Disclosure requirements are the union of all account types in the case
- Per-account status tracking within the case for operational visibility

**Acceptance Criteria:**
- A case can contain one or more account registrations
- Account registrations can be added or removed while the case is in `draft` or `pending_client_action`
- Each account registration has independent beneficiary and document requirement validation
- The submission gate validates completeness across all accounts in the case
- Approval applies to the entire case; individual accounts cannot be approved separately
- Activation transitions all account registrations to `active` in the registry
- Disclosure requirements reflect the union of all account types
- The API returns per-account status and completeness information within the case

**Dependencies:**
- Issue 6.1: case creation with account registrations
- Issue 6.4: legal parties shared across accounts
- Issue 6.5: per-account beneficiary requirements
- Issue 6.6: per-account document requirements
- Issue 6.9: batch activation

---

### Issue 6.11: Onboarding Case Status API and History

**Title:** Implement case listing, filtering, status querying, and full history retrieval

**Description:**
Advisors, operations users, and compliance staff need to query onboarding cases by status, date range, advisor, household, and other dimensions. The case detail endpoint must return the full current state including all linked parties, accounts, documents, disclosures, beneficiaries, and trusted contacts. A dedicated history endpoint returns the ordered list of all status transitions, notes, exceptions, and actions taken on the case. This provides the complete audit trail for regulatory and operational purposes.

**Scope:**
- Endpoint: `GET /api/onboarding-cases` (list with filters)
  - Filters: `status`, `advisor_id`, `household_id`, `client_id`, `created_after`, `created_before`, `updated_after`, `assigned_reviewer_id`
  - Pagination: cursor-based
  - Sort: by `created_at`, `updated_at`, or `status`
  - Permission: advisors see their own cases; operations/compliance see all within tenant
- Endpoint: `GET /api/onboarding-cases/:id` (full case detail)
  - Returns: case metadata, current status, linked parties, account registrations, disclosures with acceptance status, documents with classification, beneficiaries, trusted contacts, exception records, assigned reviewer, notes
  - Presenter layer to avoid leaking database internals
- Endpoint: `GET /api/onboarding-cases/:id/history` (ordered workflow history)
  - Returns: chronological list of all status transitions, notes added, exceptions flagged/resolved, documents attached, disclosures accepted
  - Each entry: timestamp, actor, action type, previous status, new status, metadata
- Endpoint: `POST /api/onboarding-cases/:id/add-note` (append note)
  - Accepts note text and optional category
  - Notes are append-only and immutable
  - Valid in any non-terminal status (also valid in `activated` for post-activation annotations)
- Redis caching for frequently accessed case status summaries
- Emit audit event for note additions

**Acceptance Criteria:**
- `GET /api/onboarding-cases` returns paginated results filtered by the requested dimensions
- Advisors can only see cases they own; operations and compliance users see all tenant cases
- `GET /api/onboarding-cases/:id` returns the complete current state of the case with all linked entities
- `GET /api/onboarding-cases/:id/history` returns a chronological, append-only record of all actions
- History entries include actor, timestamp, action type, and relevant metadata
- `POST .../add-note` appends an immutable note visible in case detail and history
- Notes can be added in any status including `activated` (for post-activation annotations)
- Cursor-based pagination works correctly across all list endpoints
- Response payloads use presenter serialization, not raw database rows
- Frequently accessed case summaries are cached in Redis with appropriate TTL and invalidation

**Dependencies:**
- All prior issues in this epic (6.1 through 6.10) contribute data to the case detail and history
- Epic 3: workflow history table, notes model
- Epic 1: role-based visibility scoping

---

## Database Schema Summary

Key tables introduced or extended by this epic:

| Table | Purpose |
|-------|---------|
| `onboarding_cases` | Core case record with status, tenant, advisor, household references |
| `onboarding_case_accounts` | Join table linking cases to account registrations |
| `onboarding_case_parties` | Join table linking cases to legal parties with roles |
| `onboarding_case_disclosures` | Required disclosures per case |
| `consent_records` | Immutable disclosure acceptance records |
| `onboarding_case_documents` | Join table linking cases to document vault records |
| `onboarding_case_beneficiaries` | Beneficiary designations within a case |
| `onboarding_case_trusted_contacts` | Trusted contact records within a case |
| `onboarding_case_exceptions` | Exception records with category, reason, resolution |
| `onboarding_case_notes` | Append-only notes |
| `disclosure_definitions` | Versioned disclosure metadata |

All tables include `tenant_id`, `created_at`, `updated_at`, and appropriate foreign keys. Workflow history entries are stored in the shared `workflow_history` table from Epic 3.

## Domain Events

| Event | Trigger |
|-------|---------|
| `onboarding_case.created` | Case creation |
| `onboarding_case.client_action_requested` | Transition to pending_client_action |
| `onboarding_case.client_data_submitted` | Client completes a data section |
| `onboarding_case.disclosure_accepted` | Disclosure acceptance recorded |
| `onboarding_case.party_added` | Legal party linked to case |
| `onboarding_case.party_removed` | Legal party unlinked from case |
| `onboarding_case.document_attached` | Document attached to case |
| `onboarding_case.document_detached` | Document detached from case |
| `onboarding_case.submitted` | Case submitted for review |
| `onboarding_case.exception_flagged` | Exception flagged on case |
| `onboarding_case.exception_resolved` | Exception resolved |
| `onboarding_case.approved` | Case approved |
| `onboarding_case.rejected` | Case rejected |
| `onboarding_case.activated` | Case activated, accounts go live |
| `onboarding_case.note_added` | Note appended to case |
| `account.activated` | Per-account event on activation |

## Non-Functional Requirements

- All mutating endpoints accept an idempotency key
- All state transitions are recorded in workflow history with actor, timestamp, and context
- PII fields (SSN/TIN) are encrypted at rest and masked in API responses
- All endpoints are tenant-scoped with row-level isolation
- Audit events are emitted for every state-changing operation
- Case data is never hard-deleted; soft-delete with retention policies where applicable
- Exception states are surfaced in operational dashboards (Epic 15 integration point)
- Client-facing and advisor-facing endpoints enforce distinct permission models
