# Epic 14: Client Portal Experience

## Goal

Deliver a client-facing portal with its own authentication, permission model, and simplified views over the platform's operational data. Clients must be able to view their accounts, track transfers, access documents, and perform a narrow set of self-service actions -- all under a permission model that is completely separate from the advisor permission model. Client-visible statuses and data projections must be simplified relative to the internal operational representations.

## Dependencies

- Epic 1 (Tenant, Identity, and Access Control) -- JWT infrastructure, tenant resolution, MFA primitives
- Epic 2 (Client, Household, and Account Registry) -- client person/entity records, accounts, beneficiaries, trusted contacts
- Epic 5 (Document Vault and Records Management) -- document metadata, artifact types, secure retrieval
- Epic 6 (Onboarding and Account Opening) -- invitation linkage, onboarding case status
- Epic 7 (Money Movement and Transfer Operations) -- transfer intents and lifecycle statuses
- Epic 13 (Reporting, Statements, and Snapshots) -- published statement artifacts and report retrieval

## Design Principles

1. **Separate permission model.** Client permissions are not a subset of advisor permissions. They are a distinct actor type with their own capability set, enforced independently.
2. **Simplified status projections.** Internal operational statuses (e.g., `pending_external_review`, `pending_verification`, `in_transit`) are mapped to a smaller set of client-facing statuses (e.g., `processing`, `completed`, `action_needed`).
3. **Document visibility by artifact type.** Not all documents are client-visible. Visibility is determined by artifact type and an explicit client-visible flag, not by blanket access to the document vault.
4. **Read-heavy, write-light.** The client portal is predominantly read-only. The few write actions (upload documents, verify bank, update notification preferences) are explicitly enumerated and permissioned.
5. **Tenant-scoped and firm-branded.** Every client session is tenant-scoped and the portal renders firm branding, not platform branding.

---

## Issue 14-1: Client Invitation and Activation Flow

### Title

Client invitation and activation flow

### Description

Advisors invite clients to the portal by initiating an invitation from the advisor workspace. The platform generates a secure, time-limited invitation token and delivers it via email. The client follows the link to create their portal account, sets a password, and enrolls in MFA. Upon successful activation, the client record is linked to the new portal user and an audit event is emitted.

### Scope

- `POST /api/clients/:clientId/invitations` endpoint (advisor-facing, requires `client_portal.invite` permission)
- Invitation record in Postgres: `client_portal_invitations` table with `id`, `client_id`, `tenant_id`, `email`, `token_hash`, `status` (pending, accepted, expired, revoked), `expires_at`, `created_by`, `created_at`
- Secure token generation (cryptographically random, hashed at rest, single-use)
- Invitation email dispatch via notification service integration
- `POST /api/client-portal/activate` endpoint (unauthenticated, token-validated) accepting password and MFA enrollment
- Password policy enforcement (minimum length, complexity)
- TOTP-based MFA enrollment during activation (QR code provisioning, backup codes)
- Idempotency: re-invitation to the same email revokes prior pending invitations
- `POST /api/clients/:clientId/invitations/resend` for advisor-triggered resend with idempotency key
- Invitation expiry (configurable per tenant, default 72 hours)
- Audit events: `client_invitation_sent`, `client_invitation_accepted`, `client_invitation_expired`, `client_invitation_revoked`

### Acceptance Criteria

- Advisor with `client_portal.invite` permission can send an invitation to a client's email on record
- Invitation token is hashed at rest and expires after the configured TTL
- Client can activate their account only once per invitation token
- MFA enrollment is mandatory during activation; activation does not complete without it
- Re-inviting the same client revokes any prior pending invitation
- Expired or revoked tokens return a clear error directing the client to request a new invitation
- All invitation lifecycle events are recorded in the audit log with tenant, actor, and client context
- Activation endpoint is rate-limited to prevent brute-force token guessing

### Dependencies

- Epic 1: JWT and session infrastructure, MFA primitives (TOTP enrollment)
- Epic 2: `ClientPerson` / `ClientEntity` records with email on file
- Epic 15: Notification service for email delivery (can stub with direct send initially)

---

## Issue 14-2: Client Authentication and Session Management (JWT and Actor Type)

### Title

Client authentication with separate JWT claims and actor type

### Description

Implement a client-specific authentication flow that issues tenant-scoped JWTs with a `client` actor type, distinct from advisor/staff actor types. Client tokens carry a restricted claim set that identifies the client, their tenant, and their session -- but never advisor roles or permissions. The authentication middleware must recognize the `client` actor type and route permission checks through the client permission evaluator, not the advisor permission evaluator.

### Scope

- `POST /api/client-portal/auth/login` endpoint accepting email + password, returning short-lived access token + rotating refresh token
- `POST /api/client-portal/auth/mfa/verify` endpoint for MFA challenge during login
- `POST /api/client-portal/auth/refresh` endpoint for token rotation
- `POST /api/client-portal/auth/logout` endpoint invalidating the refresh token and session
- JWT claims: `sub` (client portal user ID), `tenantId`, `actorType: "client"`, `clientId`, `sessionId`, `iat`, `exp`
- Access token TTL: short (e.g., 15 minutes); refresh token TTL: longer (e.g., 7 days), rotating
- Session record in Postgres or Redis: `client_sessions` with device fingerprint, IP, created_at, last_active_at
- Middleware updates to `actor-resolution` middleware: detect `actorType === "client"` and load client context instead of advisor/user context
- Client auth endpoints must be on a separate route group (`/api/client-portal/auth/*`) from advisor auth (`/api/auth/*`)
- Failed login lockout after configurable threshold (e.g., 5 failed attempts in 15 minutes)
- Audit events: `client_login_success`, `client_login_failed`, `client_mfa_verified`, `client_token_refreshed`, `client_logout`

### Acceptance Criteria

- Client login returns a JWT with `actorType: "client"` and `clientId` in claims
- Client JWTs are never accepted by advisor-facing endpoints; advisor JWTs are never accepted by client-facing endpoints
- MFA verification is required on every login, not just activation
- Refresh token rotation invalidates the previous refresh token on each use
- Failed login attempts are tracked and lockout is enforced after threshold
- All authentication events are audit-logged with IP and device metadata
- Client sessions can be listed and revoked (see Issue 14-10)

### Dependencies

- Issue 14-1: Client activation must be complete before login is possible
- Epic 1: JWT signing infrastructure, MFA verification logic

---

## Issue 14-3: Client Permission Model

### Title

Client permission model (read-only default, enumerated write actions)

### Description

Define and enforce a client-specific permission model that is entirely separate from the advisor role/permission system. Clients are read-only by default. A small, explicitly enumerated set of write capabilities may be granted. Permissions are evaluated by a dedicated client permission evaluator that the middleware invokes when the actor type is `client`.

### Scope

- Client capability enum:
  - `client_portal.accounts.read` -- view own accounts and balances
  - `client_portal.holdings.read` -- view own holdings
  - `client_portal.transfers.read` -- view own transfers (simplified statuses)
  - `client_portal.documents.read` -- view client-visible documents
  - `client_portal.documents.upload` -- upload requested documents
  - `client_portal.documents.download` -- download statements and client-visible artifacts
  - `client_portal.statements.read` -- view published statements
  - `client_portal.activity.read` -- view account activity
  - `client_portal.beneficiaries.read` -- view beneficiary information
  - `client_portal.trusted_contacts.read` -- view trusted contact information
  - `client_portal.bank_accounts.verify` -- complete micro-deposit or instant bank verification
  - `client_portal.notifications.read` -- view notification preferences
  - `client_portal.notifications.update` -- update notification preferences
  - `client_portal.sessions.read` -- view active sessions
  - `client_portal.sessions.revoke` -- revoke a session
  - `client_portal.profile.read` -- view own profile
- `ClientPermissionEvaluator` service that resolves capabilities from the client's activation status and any firm-level policy overrides
- Firm-level policy table (`client_portal_policies`) allowing firm admins to enable or disable optional client capabilities (e.g., disable document upload, disable beneficiary visibility)
- Permission guard middleware for client routes: `requireClientCapability("client_portal.documents.upload")`
- All client permission checks scoped to the client's own records only (no cross-client access within a household unless explicitly modeled later)

### Acceptance Criteria

- A client can only access their own records; requests for another client's data return 403
- Write actions not in the enumerated capability set are rejected
- Firm admin can configure which optional capabilities are enabled for the client portal via policy
- Permission evaluation does not reference advisor roles or advisor permission tables
- A disabled capability at the firm policy level overrides the default grant
- Middleware enforces client permissions on every client-portal route

### Dependencies

- Issue 14-2: Client actor type in JWT and middleware routing
- Epic 1: Permission enforcement middleware pattern

---

## Issue 14-4: Account Overview API

### Title

Client account overview API with simplified balances and holdings summary

### Description

Provide client-facing API endpoints that return the client's own accounts with simplified balance and holdings information. The response shapes must omit internal operational fields (sync timestamps, upstream IDs, projection metadata) and present balances in a client-friendly format. Holdings are summarized by asset class or position, not by internal lot-level detail.

### Scope

- `GET /api/client-portal/accounts` -- list client's accounts with summary balances
- `GET /api/client-portal/accounts/:accountId` -- single account detail with balance breakdown
- `GET /api/client-portal/accounts/:accountId/holdings` -- holdings summary for an account
- `GET /api/client-portal/overview` -- aggregated cross-account summary (total value, asset allocation breakdown)
- Client-facing account presenter that maps internal `Account` records to a simplified shape:
  - `id`, `accountName`, `accountType` (human-readable), `status` (simplified: active, restricted, closed), `totalValue`, `cashBalance`, `investedValue`
- Client-facing holdings presenter:
  - `symbol`, `name`, `assetClass`, `quantity`, `marketValue`, `percentOfAccount`, `dayChange`, `dayChangePercent`
  - No lot-level detail, no cost basis (unless firm policy enables it), no tax lot IDs
- All queries scoped to the authenticated client's `clientId`; cross-client data is never returned
- Balance data sourced from the balance projection layer (Epic 11) or account summary cache
- Cache strategy: Redis cache with short TTL (e.g., 60 seconds) for account summaries, cache-aside pattern
- Response includes `asOf` timestamp so the client knows the freshness of the data

### Acceptance Criteria

- Client sees only their own accounts; accounts belonging to other clients in the same household are not visible (unless household-level access is explicitly enabled later)
- Balances are presented as `totalValue`, `cashBalance`, `investedValue` without exposing `settled`, `pending`, or `available` cash distinctions
- Holdings are summarized at position level; no lot-level, cost-basis, or tax-lot details unless firm policy enables it
- Internal fields (`syncStatus`, `upstreamId`, `lastSyncedAt`, `projectionVersion`) are never included in client responses
- Account status is mapped from internal statuses to simplified set: `active`, `restricted`, `closed`
- Response includes `asOf` timestamp
- Endpoint returns 404 if the account does not belong to the authenticated client

### Dependencies

- Issue 14-3: Client permission enforcement (`client_portal.accounts.read`, `client_portal.holdings.read`)
- Epic 2: Account and client records
- Epic 11: Balance projections (can use a simplified stub until Epic 11 is complete)

---

## Issue 14-5: Transfer Visibility with Simplified Client-Facing Statuses

### Title

Client-facing transfer visibility with simplified status mapping

### Description

Expose transfer data to clients with a reduced set of statuses that abstracts away internal operational complexity. Internally, transfers may move through `draft`, `submitted`, `pending_verification`, `pending_external_review`, `in_transit`, `completed`, `failed`, `cancelled`, `reversed`, and `exception`. Clients see a simpler set that communicates what they need to know and what, if any, action is required from them.

### Scope

- `GET /api/client-portal/transfers` -- list transfers for the client's accounts, paginated
- `GET /api/client-portal/transfers/:transferId` -- single transfer detail
- Client-facing transfer status mapping:

  | Internal Status              | Client-Facing Status | Client-Facing Label       |
  |------------------------------|----------------------|---------------------------|
  | `draft`                      | not visible          | (hidden from client)      |
  | `submitted`                  | `processing`         | "Processing"              |
  | `pending_verification`       | `action_needed`      | "Action Needed"           |
  | `pending_external_review`    | `processing`         | "Processing"              |
  | `in_transit`                 | `processing`         | "In Transit"              |
  | `completed`                  | `completed`          | "Completed"               |
  | `failed`                     | `failed`             | "Failed"                  |
  | `cancelled`                  | `cancelled`          | "Cancelled"               |
  | `reversed`                   | `reversed`           | "Reversed"                |
  | `exception`                  | `processing`         | "Processing"              |

- Client-facing transfer presenter:
  - `id`, `type` (human-readable: "ACH Deposit", "ACAT Transfer", etc.), `status`, `statusLabel`, `amount` (if applicable), `fromLabel`, `toLabel`, `initiatedAt`, `completedAt`, `actionRequired` (boolean), `actionDescription` (if action_needed)
- Draft transfers are excluded from client visibility entirely
- Exception transfers appear as "Processing" to the client to avoid exposing internal operational states
- `action_needed` status includes a human-readable description of what the client must do (e.g., "Please verify your bank account via micro-deposits")
- Transfers scoped to accounts belonging to the authenticated client only

### Acceptance Criteria

- Client never sees `draft` or `exception` as a status
- Internal statuses are mapped to the simplified client-facing set; no internal status string is ever returned
- Transfers in `pending_verification` show `action_needed` with a description of the required client action
- Transfer list is paginated with cursor-based pagination
- Each transfer includes human-readable type labels, not internal enum values
- Client can only see transfers on their own accounts
- Presenter omits internal fields: `upstreamRailId`, `syncStatus`, `exceptionNotes`, `internalReviewStatus`

### Dependencies

- Issue 14-3: Client permission enforcement (`client_portal.transfers.read`)
- Epic 7: Transfer records and lifecycle statuses
- Issue 14-4: Account ownership verification (same scoping logic)

---

## Issue 14-6: Document Vault Access for Clients

### Title

Client document vault access with type-based visibility rules

### Description

Clients can access a subset of documents in the vault: published statements, tax documents, trade confirmations, and documents they have uploaded. Internal operational documents (compliance notes, internal review artifacts, exception attachments) are not client-visible. Visibility is controlled by artifact type and an explicit `clientVisible` flag on the document metadata record. Clients can also upload documents that have been requested by their advisor (e.g., proof of identity, signed forms).

### Scope

- `GET /api/client-portal/documents` -- list client-visible documents, filterable by type and date range
- `GET /api/client-portal/documents/:documentId` -- document metadata for a client-visible document
- `GET /api/client-portal/documents/:documentId/download` -- generate a time-limited signed URL for download
- `POST /api/client-portal/documents/upload` -- upload a document in response to an advisor request
- Client-visible artifact types:
  - `statement` -- account statements
  - `tax_document` -- 1099s, year-end tax forms
  - `trade_confirmation` -- trade confirms
  - `client_upload` -- documents uploaded by the client
  - `signed_agreement` -- executed agreements and disclosures
- Non-client-visible artifact types (filtered out):
  - `internal_review_note`
  - `compliance_artifact`
  - `exception_attachment`
  - `onboarding_draft`
- `clientVisible` boolean flag on `DocumentRecord` metadata, set when the document is created or published
- Upload flow:
  - Client uploads to a presigned URL (S3 or equivalent)
  - Platform creates a `DocumentRecord` with `source: "client_upload"`, `status: "pending_review"`, linked to the client and optionally to a requesting case or task
  - Advisor is notified of the upload
- Download flow:
  - Verify document belongs to the client and is client-visible
  - Generate a short-lived signed URL (e.g., 5-minute expiry)
  - Emit audit event: `client_document_downloaded`
- Audit events: `client_document_viewed`, `client_document_downloaded`, `client_document_uploaded`

### Acceptance Criteria

- Client sees only documents where `clientVisible = true` and the document is linked to their client record or accounts
- Documents with non-client-visible artifact types are never returned, even if `clientVisible` is inadvertently true
- Client can upload documents only when a corresponding upload request or open task exists (prevents unsolicited uploads unless firm policy allows open uploads)
- Download URLs are time-limited and single-use where possible
- Every document view and download emits an audit event
- Upload creates a `pending_review` document record and notifies the advisor
- Client cannot delete or modify documents; uploads are append-only

### Dependencies

- Issue 14-3: Client permission enforcement (`client_portal.documents.read`, `client_portal.documents.upload`, `client_portal.documents.download`)
- Epic 5: Document vault infrastructure, presigned URL generation, artifact type taxonomy

---

## Issue 14-7: Activity and Statement Viewing

### Title

Client activity feed and statement viewing

### Description

Provide clients with a chronological activity feed for their accounts and access to published statement artifacts. Activity includes transactions, transfers, dividends, interest, and fee debits -- presented with client-friendly descriptions. Statements are immutable published artifacts retrieved from the document vault.

### Scope

- `GET /api/client-portal/accounts/:accountId/activity` -- paginated activity feed for a single account
- `GET /api/client-portal/activity` -- cross-account activity feed for the client, paginated
- `GET /api/client-portal/statements` -- list published statements for the client's accounts
- `GET /api/client-portal/statements/:statementId/download` -- download a published statement artifact
- Activity entry presenter:
  - `id`, `date`, `type` (human-readable: "Purchase", "Sale", "Dividend", "Deposit", "Withdrawal", "Fee", "Interest"), `description`, `amount`, `balance` (running balance if available), `accountName`
- Activity types surfaced to clients:
  - trade executions (buy/sell, presented as "Purchase"/"Sale")
  - cash movements (deposits, withdrawals)
  - dividends and interest
  - fee debits
  - corporate actions (splits, mergers -- human-readable descriptions)
- Activity types not surfaced to clients:
  - internal reconciliation adjustments
  - system-generated correction entries
  - compliance holds (unless they affect available balance, in which case show as "Pending")
- Statement list presenter:
  - `id`, `accountName`, `periodStart`, `periodEnd`, `type` ("Monthly Statement", "Quarterly Statement", "Tax Document"), `publishedAt`, `downloadAvailable`
- Pagination: cursor-based, default page size 50, max 200
- Date range filtering on activity endpoints

### Acceptance Criteria

- Activity feed shows client-relevant entries only; internal adjustments and corrections are excluded
- Activity descriptions are human-readable, not internal event codes
- Statements are only visible once published; draft or in-progress statement runs are not shown
- Statement download delegates to the document vault with signed URL generation and audit logging
- Activity is scoped to the client's own accounts
- Pagination works correctly with cursor-based navigation
- Date range filters are supported on activity endpoints

### Dependencies

- Issue 14-3: Client permissions (`client_portal.activity.read`, `client_portal.statements.read`)
- Issue 14-6: Document download infrastructure (signed URLs, audit events)
- Epic 13: Published statement artifacts
- Epic 11: Activity/transaction data from ledger or activity projection

---

## Issue 14-8: Beneficiary and Trusted Contact Review

### Title

Client view of beneficiary and trusted contact information

### Description

Clients can view beneficiary designations and trusted contact information associated with their accounts. Edit capability depends on firm-level policy: some firms allow clients to request beneficiary changes through the portal (creating a review task for the advisor), while others require all changes to go through the advisor directly. Trusted contact information is always view-only for clients.

### Scope

- `GET /api/client-portal/accounts/:accountId/beneficiaries` -- list beneficiaries for an account
- `GET /api/client-portal/accounts/:accountId/trusted-contacts` -- list trusted contacts for an account
- `POST /api/client-portal/accounts/:accountId/beneficiaries/change-request` -- submit a beneficiary change request (if firm policy allows)
- Beneficiary presenter:
  - `id`, `name`, `relationship`, `designation` ("Primary", "Contingent"), `percentage`, `status` ("Active", "Pending Change")
- Trusted contact presenter:
  - `id`, `name`, `relationship`, `phone`, `email`
- Beneficiary change request flow (when enabled):
  - Client submits requested changes (add, modify, remove beneficiary)
  - Platform creates an `OperationalTask` assigned to the advisor with type `beneficiary_change_request`
  - Client sees the beneficiary status as "Pending Change" until the advisor processes the request
  - Advisor approves or rejects the change through the advisor workspace
  - Client is notified of the outcome
- Firm policy flag: `client_portal_policies.beneficiary_change_requests_enabled` (default: false)
- Trusted contacts are strictly read-only for clients; any changes require advisor action
- Audit events: `client_beneficiary_viewed`, `client_beneficiary_change_requested`, `client_trusted_contact_viewed`

### Acceptance Criteria

- Client can view beneficiaries and trusted contacts for their own accounts
- Trusted contact information is always read-only; no edit or change-request endpoint is exposed
- Beneficiary change requests are only accepted if the firm policy enables them
- A beneficiary change request creates an operational task for the advisor, not a direct data mutation
- Client sees "Pending Change" status on beneficiaries with outstanding change requests
- Attempting to submit a change request when firm policy disables it returns 403 with a clear message
- All views emit audit events

### Dependencies

- Issue 14-3: Client permissions (`client_portal.beneficiaries.read`, `client_portal.trusted_contacts.read`)
- Epic 2: Beneficiary and trusted contact records
- Epic 3: Operational task creation for change requests

---

## Issue 14-9: Client Notification Preferences

### Title

Client notification preference management

### Description

Clients can view and update their notification preferences for the portal. Preferences control which notifications the client receives and through which channels (email, push, in-app). The platform must respect firm-level mandatory notifications (e.g., security alerts, regulatory notices) that cannot be disabled by the client.

### Scope

- `GET /api/client-portal/notification-preferences` -- retrieve current preferences
- `PUT /api/client-portal/notification-preferences` -- update preferences
- Notification categories:
  - `security_alerts` -- login from new device, password changes, MFA changes (mandatory, cannot be disabled)
  - `transfer_updates` -- transfer status changes (default: on)
  - `statement_available` -- new statement published (default: on)
  - `document_requests` -- advisor requests a document upload (default: on)
  - `account_updates` -- account status changes (default: on)
  - `marketing` -- firm communications and educational content (default: off)
- Channels per category: `email`, `push` (for future mobile), `in_app`
- Preference record: `client_notification_preferences` table with `client_id`, `tenant_id`, `category`, `channel`, `enabled`, `updated_at`
- Mandatory categories are enforced server-side; even if a client sends `enabled: false` for a mandatory category, the server ignores it and keeps it enabled
- Default preferences are seeded during client activation (Issue 14-1)
- Zod validation on the update payload to reject unknown categories or channels

### Acceptance Criteria

- Client can view all notification categories with their current enabled/disabled state per channel
- Client can update preferences for non-mandatory categories
- Mandatory categories (`security_alerts`) cannot be disabled; update requests that attempt to disable them are silently ignored or return a warning
- Default preferences are created during activation
- Preferences are tenant-scoped and client-scoped
- Unknown categories or channels in the update payload are rejected with a validation error

### Dependencies

- Issue 14-2: Client authentication (preferences are per-authenticated-client)
- Issue 14-3: Client permissions (`client_portal.notifications.read`, `client_portal.notifications.update`)
- Epic 15: Notification routing system (preferences are consumed by the notification service)

---

## Issue 14-10: Session and Device Management for Clients

### Title

Client session and device management

### Description

Clients can view their active sessions and registered devices, and revoke sessions they do not recognize. This is a security feature that gives clients visibility into where their account is logged in and the ability to terminate suspicious sessions.

### Scope

- `GET /api/client-portal/sessions` -- list active sessions for the authenticated client
- `DELETE /api/client-portal/sessions/:sessionId` -- revoke a specific session (logout that device)
- `POST /api/client-portal/sessions/revoke-all` -- revoke all sessions except the current one
- Session record fields exposed to client:
  - `sessionId`, `deviceType` ("Desktop", "Mobile", "Tablet"), `browser`, `os`, `ipAddress` (masked to city/region, not full IP), `lastActiveAt`, `createdAt`, `isCurrent` (boolean)
- Session metadata captured at login:
  - User-Agent parsing for device, browser, OS
  - IP geolocation for approximate location (city/region level)
  - Timestamp of creation and last activity
- Revoking a session:
  - Invalidates the refresh token for that session
  - Adds the session's current access token to a short-lived Redis deny list (for the remaining access token TTL)
  - Emits audit event: `client_session_revoked`
- Revoke-all:
  - Invalidates all refresh tokens except the current session
  - Emits audit event: `client_all_sessions_revoked`
- Security alert: when a new session is created from an unrecognized device or location, send a `security_alerts` notification to the client (ties into Issue 14-9 mandatory notifications)

### Acceptance Criteria

- Client can see all active sessions with device, location (approximate), and last activity time
- Client can revoke individual sessions; the revoked session's tokens become immediately invalid
- "Revoke all" terminates all sessions except the current one
- Full IP addresses are not exposed to the client; only approximate location is shown
- New-device login triggers a security alert notification
- All session revocation actions are audit-logged
- A revoked session's access token is denied even before natural expiry (via Redis deny list)

### Dependencies

- Issue 14-2: Client session records and JWT infrastructure
- Issue 14-9: Security alert notifications for new device login
- Epic 1: Redis infrastructure for token deny list

---

## Issue 14-11: Co-Branded Experience Support

### Title

Firm branding applied to client portal

### Description

The client portal renders with the firm's branding -- logo, colors, firm name, and contact information -- rather than platform-default branding. Branding configuration is managed by firm admins through the advisor workspace and applied dynamically to the client portal at runtime. This ensures every client sees their advisor's firm identity, not the underlying platform identity.

### Scope

- `GET /api/client-portal/branding` -- public (unauthenticated) endpoint that returns firm branding for the current tenant, resolved via subdomain or tenant context
- Branding payload:
  - `firmName` -- display name
  - `logoUrl` -- URL to firm logo (served from object storage via CDN)
  - `faviconUrl` -- URL to firm favicon
  - `primaryColor` -- hex color for primary UI accents
  - `secondaryColor` -- hex color for secondary UI accents
  - `contactEmail` -- firm's client-facing support email
  - `contactPhone` -- firm's client-facing support phone
  - `disclaimerText` -- optional legal disclaimer shown in portal footer
  - `loginMessage` -- optional custom message on the login page
- Branding data sourced from the `FirmBranding` resource (Epic 1 / core resource model)
- Advisor-facing management (out of scope for this issue, covered in Epic 8):
  - `PUT /api/firms/current/branding` -- firm admin updates branding
  - Logo and favicon upload to object storage with size and format validation
- Client portal serves branding on all pages:
  - Login / activation page
  - Navigation header
  - Footer with disclaimer and contact info
  - Email templates (invitation, notifications) include firm branding
- Fallback behavior: if no firm branding is configured, use platform default branding
- Branding endpoint is cached aggressively (Redis, 1-hour TTL, invalidated on update)
- Branding endpoint does not require authentication (it is needed on the login page before the client has a token)

### Acceptance Criteria

- Client portal login page displays the firm's logo, name, colors, and optional login message
- All authenticated pages display firm branding in navigation and footer
- Branding endpoint is unauthenticated and resolves the correct tenant via subdomain
- If no custom branding is configured, platform defaults are returned gracefully
- Branding updates by firm admin are reflected in the portal within a reasonable cache TTL (1 hour or on cache invalidation)
- Email templates for client communications (invitations, notifications) use firm branding
- Logo and favicon are served from object storage / CDN, not inline in the API response

### Dependencies

- Epic 1: Tenant resolution via subdomain, `FirmBranding` resource
- Epic 8: Firm admin branding management (the write side; this issue covers the client-portal read side)
- Epic 15: Email template rendering with firm branding for client notifications

---

## Summary

| Issue   | Title                                              | Primary Actor | Read/Write |
|---------|----------------------------------------------------|---------------|------------|
| 14-1    | Client invitation and activation flow              | Advisor + Client | Write |
| 14-2    | Client authentication (JWT, actor type)            | Client        | Write      |
| 14-3    | Client permission model                            | System        | Config     |
| 14-4    | Account overview API                               | Client        | Read       |
| 14-5    | Transfer visibility (simplified statuses)          | Client        | Read       |
| 14-6    | Document vault access                              | Client        | Read + Upload |
| 14-7    | Activity and statement viewing                     | Client        | Read       |
| 14-8    | Beneficiary and trusted contact review             | Client        | Read + Request |
| 14-9    | Client notification preferences                    | Client        | Read + Write |
| 14-10   | Session and device management                      | Client        | Read + Write |
| 14-11   | Co-branded experience support                      | System        | Read       |

## Implementation Notes

- **Route grouping**: All client portal endpoints live under `/api/client-portal/*` with dedicated middleware that enforces `actorType === "client"` and applies the client permission evaluator. This route group must never share middleware chains with advisor routes.
- **Status mapping should be centralized**: Create a `ClientStatusMapper` utility used by all client-facing presenters. Internal status enums should never leak into client response shapes. This applies to transfers, accounts, onboarding cases, and documents.
- **Presenter layer is mandatory**: Every client-facing endpoint must use a dedicated presenter that strips internal fields. Do not reuse advisor-facing presenters and hope that optional fields will be omitted.
- **Audit coverage**: Every client-portal endpoint should emit audit events. The client portal is a regulated access surface; access patterns must be reconstructable.
- **Testing strategy**: Client permission isolation must be tested explicitly -- write tests that verify a client JWT cannot access advisor endpoints, cannot see other clients' data, and cannot perform actions outside their capability set.
