# Epic 1: Tenant, Identity, and Access Control

## Goal

Establish tenant isolation, authentication, within-tenant authorization, session security, and privileged access controls. This epic is the foundation for every other epic in the platform. No other domain can function without tenant resolution, authenticated users, enforced permissions, and auditable access.

## Dependencies

None. This is the first epic in the delivery order.

---

## Issue 1: Tenant Provisioning and Lifecycle

### Description

Implement the `Firm` entity and its provisioning workflow. A firm represents a single RIA tenant on the platform. This issue covers creating firms with unique slugs, managing firm status transitions (active, suspended, deactivated), and storing firm branding metadata. The firm record is the root of all tenant-scoped data.

### Scope

- Postgres schema for `firms` table: `id` (UUID), `name`, `slug` (unique), `status`, `branding` (JSONB), `created_at`, `updated_at`
- Slug validation rules: lowercase alphanumeric plus hyphens, minimum 3 characters, maximum 48 characters, globally unique
- Firm status enum: `provisioning`, `active`, `suspended`, `deactivated`
- `POST /api/firms` endpoint for firm creation (platform-admin-only in a super-admin context or seed script)
- `GET /api/firms/current` endpoint returning the resolved firm for the current request
- `PATCH /api/firms/current` endpoint for firm metadata updates (name, branding)
- `POST /api/firms/current/suspend` and `POST /api/firms/current/activate` for status transitions
- Service layer (`firms/service.ts`) and repository layer (`firms/repository.ts`) following the module shape convention
- Zod schemas for all request and response payloads (`firms/schemas.ts`)

### Acceptance Criteria

- Creating a firm with a valid slug returns `201` with the firm record including `id`, `name`, `slug`, and `status: "provisioning"`
- Creating a firm with a duplicate slug returns `409` with error code `IDEMPOTENCY_CONFLICT`
- Creating a firm with an invalid slug (uppercase, special characters, too short) returns `422` with error code `VALIDATION_ERROR`
- `GET /api/firms/current` returns the firm resolved from the current request's tenant context
- Suspending an active firm sets `status` to `suspended`; suspending a firm that is not active returns `422` with error code `INVALID_WORKFLOW_STATE`
- Activating a suspended firm sets `status` to `active`
- The `firms` table has a unique index on `slug` and a partial index on `status = 'active'`
- All firm mutations emit audit events (covered in Issue 12)

### Dependencies

None.

---

## Issue 2: Subdomain Resolution Middleware

### Description

Implement Hono middleware that extracts the tenant slug from the incoming request's `Host` header, resolves it to a firm record, and injects the tenant context into the request. This middleware runs second in the pipeline (after request ID assignment) and rejects requests that cannot be resolved to an active tenant.

### Scope

- Middleware that parses `{slug}.wealthadvisor.com` from the `Host` header
- Lookup of the firm by slug, checking that `status` is `active`
- Injection of `tenantId` and `firmSlug` into Hono's request context (`c.set()`)
- Redis cache layer for slug-to-firm resolution with short TTL (e.g., 60 seconds) to avoid per-request DB hits
- Cache invalidation on firm status change
- Error responses: `TENANT_NOT_FOUND` (404) when slug does not match any firm, `TENANT_NOT_FOUND` (403) when firm exists but is suspended or deactivated
- Support for local development override (e.g., `X-Tenant-Slug` header or environment variable bypass)
- Placement in middleware chain: after request ID middleware, before authentication middleware

### Acceptance Criteria

- A request to `acme.wealthadvisor.com` resolves to the firm with slug `acme` and sets `tenantId` in the request context
- A request to `unknown.wealthadvisor.com` returns `404` with error code `TENANT_NOT_FOUND`
- A request to a subdomain matching a suspended firm returns `403` with error code `TENANT_NOT_FOUND` and a message indicating the firm is not active
- After a firm is suspended, subsequent requests to its subdomain are rejected within the cache TTL window (at most 60 seconds)
- In local development mode, setting `X-Tenant-Slug: acme` on a `localhost` request resolves the tenant correctly
- The middleware sets `tenantId` before downstream middleware (auth, permissions) executes
- Redis cache hit avoids a Postgres query; cache miss falls through to Postgres and populates the cache

### Dependencies

- Issue 1 (Tenant Provisioning and Lifecycle)

---

## Issue 3: User Registration and Invitation Flow

### Description

Implement user creation via invitation. Firm admins invite users by email, which generates a time-limited invitation token. The invited user completes registration by setting a password and accepting the invitation. This issue covers the invitation lifecycle, user record creation, and initial role assignment at registration time.

### Scope

- Postgres schema for `users` table: `id` (UUID), `firm_id` (FK), `email` (unique per firm), `password_hash`, `display_name`, `status` (`invited`, `active`, `disabled`), `created_at`, `updated_at`
- Postgres schema for `invitations` table: `id` (UUID), `firm_id`, `email`, `role` (initial role to assign), `invited_by` (FK to users), `token_hash`, `expires_at`, `accepted_at`, `status` (`pending`, `accepted`, `expired`, `revoked`)
- `POST /api/users/invitations` endpoint: requires `firm_admin` role; accepts `email`, `role`, `display_name`
- `POST /api/users/register` endpoint: accepts invitation token, password, and optional profile fields; creates the user record, assigns the initial role, and marks the invitation as accepted
- `GET /api/users` endpoint: list users in the current tenant with pagination and role filter
- `GET /api/users/:id` endpoint: get a single user's profile and role assignments
- `PATCH /api/users/:id` endpoint: update display name, status (disable/enable by firm_admin)
- Invitation token generation: cryptographically random, stored as a hash, with configurable expiration (default 72 hours)
- Invitation resend: `POST /api/users/invitations/:id/resend` generates a new token and resets expiration
- Idempotency: re-inviting an email that has a pending invitation returns the existing invitation rather than creating a duplicate
- Zod schemas for all endpoints

### Acceptance Criteria

- A `firm_admin` can create an invitation for a new email; the response includes the invitation ID and status `pending`
- A non-`firm_admin` user calling `POST /api/users/invitations` receives `403` with error code `FORBIDDEN`
- Registering with a valid, unexpired invitation token creates a user with `status: "active"` and the role specified in the invitation
- Registering with an expired invitation token returns `422` with a clear error message
- Registering with an already-accepted invitation token returns `409`
- A user's email must be unique within the firm; inviting a duplicate email for an active user returns `409`
- `GET /api/users` returns only users belonging to the requesting user's firm (tenant isolation)
- Resending an invitation generates a new token hash and updates `expires_at`; the old token becomes invalid
- Disabling a user via `PATCH /api/users/:id` sets status to `disabled`; disabled users cannot authenticate (enforced in Issue 4)
- Password is stored using bcrypt or argon2 with appropriate cost factor; plaintext passwords are never persisted or logged

### Dependencies

- Issue 1 (Tenant Provisioning and Lifecycle)
- Issue 2 (Subdomain Resolution Middleware)

---

## Issue 4: JWT Access Token and Refresh Token System

### Description

Implement tenant-scoped JWT authentication. Users authenticate with email and password to receive a short-lived access token and a long-lived rotating refresh token. Access tokens carry the claims required by downstream middleware. Refresh tokens are stored in Postgres and support rotation and revocation.

### Scope

- `POST /api/auth/login` endpoint: accepts `email` and `password`, validates credentials within the resolved tenant, returns `access_token` and `refresh_token`
- `POST /api/auth/refresh` endpoint: accepts a refresh token, validates it, rotates it (issues a new refresh token and invalidates the old one), returns a new access token and refresh token pair
- `POST /api/auth/logout` endpoint: revokes the current session's refresh token
- JWT access token claims: `sub` (user ID), `tid` (tenant ID), `act` (actor type: `user`, `service`, `impersonator`), `sid` (session ID), `roles` (array of role names), `iat`, `exp`
- Access token expiration: 15 minutes
- Refresh token expiration: 7 days
- Postgres schema for `refresh_tokens` table: `id`, `user_id` (FK), `firm_id` (FK), `session_id`, `token_hash`, `expires_at`, `revoked_at`, `created_at`
- Refresh token rotation: on each refresh, the old token is marked revoked and a new one is issued; reuse of a revoked token triggers revocation of all tokens in that session (rotation theft detection)
- Authentication middleware (position 3 in the pipeline): extracts `Authorization: Bearer <token>`, verifies signature and expiration, injects `userId`, `tenantId`, `actorType`, `sessionId`, and `roles` into request context
- Reject login for users with `status: "disabled"` or `status: "invited"`
- Reject login when the resolved tenant does not match the user's `firm_id`

### Acceptance Criteria

- Successful login returns a JWT access token with claims `sub`, `tid`, `act: "user"`, `sid`, `roles`, `iat`, and `exp`
- Access token expires in 15 minutes; a request with an expired token returns `401` with error code `UNAUTHORIZED`
- A valid refresh token returns a new access/refresh token pair; the old refresh token is invalidated
- Reusing a previously rotated (revoked) refresh token invalidates all refresh tokens for that session and returns `401`
- Logging out revokes the refresh token; subsequent refresh attempts with that token return `401`
- Login with incorrect password returns `401` with error code `UNAUTHORIZED` (no distinction between "user not found" and "wrong password")
- Login for a disabled user returns `403`
- Login against a tenant that does not match the user's firm returns `401`
- The authentication middleware rejects requests without a Bearer token with `401`
- The authentication middleware rejects requests with a malformed or tampered token with `401`
- JWT signing uses RS256 or ES256 with a configurable key pair; symmetric HS256 is not used in production

### Dependencies

- Issue 1 (Tenant Provisioning and Lifecycle)
- Issue 2 (Subdomain Resolution Middleware)
- Issue 3 (User Registration and Invitation Flow)

---

## Issue 5: MFA Enrollment and Verification

### Description

Implement multi-factor authentication for all advisor-facing users. The platform requires MFA for all advisors as stated in the security and control model. This issue covers TOTP-based MFA enrollment, verification during login, and recovery code generation. MFA must be enforced before issuing full access tokens.

### Scope

- Postgres schema for `mfa_factors` table: `id`, `user_id` (FK), `type` (`totp`), `secret_encrypted`, `verified_at`, `created_at`
- Postgres schema for `mfa_recovery_codes` table: `id`, `user_id`, `code_hash`, `used_at`
- `POST /api/auth/mfa/enroll` endpoint: generates a TOTP secret, returns the provisioning URI and QR code data; requires an authenticated session
- `POST /api/auth/mfa/verify` endpoint: accepts a TOTP code, verifies it against the enrolled secret, marks the factor as verified
- Login flow modification: if MFA is enrolled, `POST /api/auth/login` returns a partial session token (limited claims, `mfa_verified: false`) instead of full tokens; the client must call `POST /api/auth/mfa/challenge` with the TOTP code to upgrade to a full session
- `POST /api/auth/mfa/challenge` endpoint: accepts `session_token` and `totp_code`, verifies the code, issues full access and refresh tokens
- Recovery codes: generated during enrollment (8 single-use codes), stored as hashes, usable in place of TOTP during challenge
- `POST /api/auth/mfa/recover` endpoint: accepts a recovery code, validates, and issues full tokens; marks the recovery code as used
- MFA enforcement policy: configurable per firm (required, optional); default is required for all users with roles `firm_admin`, `advisor`, `trader`, `operations`, `billing_admin`
- JWT claim `mfa` (boolean) indicating whether MFA was completed for the session

### Acceptance Criteria

- Enrolling in MFA returns a TOTP provisioning URI and a set of 8 recovery codes
- Verifying enrollment with a valid TOTP code sets `verified_at` on the MFA factor record
- After MFA is enrolled, login returns a partial session (no access to protected resources) until MFA challenge is completed
- A valid TOTP code submitted to `/api/auth/mfa/challenge` issues full access and refresh tokens with `mfa: true` claim
- An invalid TOTP code returns `401`; after 5 consecutive failures within 15 minutes, the challenge endpoint is temporarily locked for that session
- A valid recovery code submitted to `/api/auth/mfa/recover` issues full tokens and marks the recovery code as used; reuse returns `401`
- Users whose role requires MFA but who have not enrolled are redirected to enrollment on login (the partial session allows only MFA enrollment endpoints)
- The `mfa` claim in the JWT is `true` only when MFA was completed; permission middleware can gate sensitive operations on this claim
- TOTP secrets are encrypted at rest (application-level encryption before storage)

### Dependencies

- Issue 3 (User Registration and Invitation Flow)
- Issue 4 (JWT Access Token and Refresh Token System)

---

## Issue 6: Role and Permission Model

### Description

Implement the role-based access control model with capability-based permissions. Each user is assigned one or more roles within their tenant, and each role maps to a set of fine-grained permissions. This issue covers the data model, role-to-permission mapping, role assignment APIs, and the permission evaluation function used by enforcement middleware.

### Scope

- Postgres schema for `roles` table: `id`, `name` (unique), `description`, `is_system` (boolean, true for built-in roles), `created_at`
- Seed data for system roles: `firm_admin`, `advisor`, `trader`, `operations`, `billing_admin`, `viewer`, `support_impersonator`
- Postgres schema for `permissions` table: `id`, `name` (unique), `description`, `created_at`
- Seed data for permissions: `client.read`, `client.write`, `account.open`, `account.read`, `account.write`, `transfer.submit`, `transfer.cancel`, `order.submit`, `order.cancel`, `billing.read`, `billing.post`, `billing.reverse`, `report.read`, `report.publish`, `document.read`, `document.read_sensitive`, `document.write`, `user.read`, `user.invite`, `user.manage_roles`, `firm.read`, `firm.update`, `support.impersonate`, `mfa.manage`
- Postgres schema for `role_permissions` table: `role_id` (FK), `permission_id` (FK), unique composite key
- Postgres schema for `user_role_assignments` table: `id`, `user_id` (FK), `firm_id` (FK), `role_id` (FK), `assigned_by` (FK to users), `assigned_at`, `revoked_at`
- Default role-permission mappings:
  - `firm_admin`: all permissions
  - `advisor`: `client.read`, `client.write`, `account.open`, `account.read`, `account.write`, `transfer.submit`, `order.submit`, `billing.read`, `report.read`, `document.read`, `document.write`, `user.read`
  - `trader`: `client.read`, `account.read`, `order.submit`, `order.cancel`, `document.read`
  - `operations`: `client.read`, `client.write`, `account.read`, `account.write`, `transfer.submit`, `transfer.cancel`, `document.read`, `document.write`, `user.read`
  - `billing_admin`: `client.read`, `account.read`, `billing.read`, `billing.post`, `billing.reverse`, `report.read`
  - `viewer`: `client.read`, `account.read`, `billing.read`, `report.read`, `document.read`
  - `support_impersonator`: `support.impersonate`, `client.read`, `account.read`, `user.read`
- `PUT /api/users/:id/roles` endpoint: requires `user.manage_roles` permission; accepts an array of role names; replaces current role assignments
- `GET /api/users/:id/roles` endpoint: returns current role assignments
- Permission evaluation function: `hasPermission(userRoles, requiredPermission) => boolean` and `hasAnyPermission(userRoles, permissions[]) => boolean`
- Actor and role resolution middleware (position 4 in the pipeline): after authentication, loads the user's active role assignments and resolved permissions, injects them into the request context
- Redis cache for user role/permission resolution with short TTL; invalidated on role assignment change

### Acceptance Criteria

- The seven system roles are seeded on database migration and cannot be deleted via API
- The permission seed includes all listed permissions; new permissions can be added via migration only
- `PUT /api/users/:id/roles` with `["advisor", "trader"]` assigns both roles; previous assignments are soft-revoked (`revoked_at` set)
- Only users with `user.manage_roles` permission can call `PUT /api/users/:id/roles`; others receive `403`
- `hasPermission(["advisor"], "order.submit")` returns `true`
- `hasPermission(["viewer"], "order.submit")` returns `false`
- `hasPermission(["firm_admin"], "support.impersonate")` returns `true` (firm_admin has all permissions)
- A user with multiple roles receives the union of all permissions from those roles
- Role assignment changes take effect within the Redis cache TTL (configurable, default 30 seconds)
- The actor/role resolution middleware injects `permissions: string[]` into the request context after authentication
- Role assignment audit events are emitted (covered in Issue 12)

### Dependencies

- Issue 3 (User Registration and Invitation Flow)
- Issue 4 (JWT Access Token and Refresh Token System)

---

## Issue 7: Permission Enforcement Middleware

### Description

Implement Hono middleware and route-level guards that enforce permission checks before handler execution. This middleware sits at position 5 in the request pipeline and uses the permissions resolved by the actor/role middleware. It supports both route-level permission requirements and programmatic checks within service logic.

### Scope

- Route-level permission guard: a Hono middleware factory `requirePermission(...permissions: string[])` that checks the request context for the required permission(s) and returns `403` with error code `FORBIDDEN` if not present
- Support for `requireAnyPermission(...permissions: string[])` (OR logic) and `requireAllPermissions(...permissions: string[])` (AND logic)
- MFA gate: a middleware `requireMfa()` that checks the `mfa` claim on the JWT and returns `403` if MFA was not completed for the session
- Programmatic permission check: an exported function `assertPermission(ctx, permission)` that can be called inside service methods for dynamic authorization decisions
- Integration with route definitions: permissions are declared per route, e.g., `app.post('/api/transfers', requirePermission('transfer.submit'), handler)`
- Error response format: `{ error: { code: "FORBIDDEN", message: "Missing required permission: transfer.submit" } }`
- No permission check on public routes: `/api/auth/login`, `/api/auth/refresh`, `/api/auth/mfa/challenge`, `/api/auth/mfa/recover`, `/api/users/register`
- Middleware ordering enforcement: this middleware must run after authentication (position 3) and actor/role resolution (position 4)

### Acceptance Criteria

- A request from a user with role `viewer` to `POST /api/transfers` (requires `transfer.submit`) returns `403` with error code `FORBIDDEN` and message indicating the missing permission
- A request from a user with role `advisor` to `POST /api/transfers` passes the permission guard
- `requireAnyPermission('order.submit', 'order.cancel')` passes if the user has either permission
- `requireAllPermissions('billing.read', 'billing.post')` fails if the user has only `billing.read`
- `requireMfa()` returns `403` when the JWT `mfa` claim is `false` or absent
- Public auth routes are accessible without any Bearer token
- The error response body includes the specific permission that was missing
- Permission enforcement runs after tenant resolution and authentication; a request with no auth token is rejected at the auth layer (401) before reaching permission enforcement
- Programmatic `assertPermission(ctx, 'document.read_sensitive')` throws a `ForbiddenError` caught by the error handler if the user lacks the permission

### Dependencies

- Issue 4 (JWT Access Token and Refresh Token System)
- Issue 5 (MFA Enrollment and Verification)
- Issue 6 (Role and Permission Model)

---

## Issue 8: Rate Limiting (Per-Tenant and Per-User)

### Description

Implement rate limiting middleware using Redis to protect the platform from abuse and ensure fair usage across tenants. Rate limits are enforced at two levels: per-tenant (aggregate across all users of a firm) and per-user (individual user within a tenant). This middleware sits at position 6 in the pipeline, after permission enforcement.

### Scope

- Redis-backed sliding window or token bucket rate limiter
- Per-tenant rate limit: configurable requests per minute per firm (default: 1000 req/min)
- Per-user rate limit: configurable requests per minute per user (default: 100 req/min)
- Rate limit headers on all responses: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- Rate limit exceeded response: `429 Too Many Requests` with error code `RATE_LIMITED` and a `Retry-After` header
- Redis key structure: `ratelimit:tenant:{tenantId}` and `ratelimit:user:{userId}`
- Configurable per-endpoint overrides for sensitive endpoints (e.g., `/api/auth/login` limited to 10 req/min per IP to mitigate brute force)
- Login-specific rate limit by IP address (pre-authentication, so no user context is available)
- Bypass for service-to-service calls authenticated via service tokens (Issue 9)
- Rate limit configuration stored per firm in the `firms` table or a `firm_settings` table

### Acceptance Criteria

- A user exceeding 100 requests in a 1-minute window receives `429` with error code `RATE_LIMITED` and a `Retry-After` header
- A tenant exceeding 1000 aggregate requests in a 1-minute window causes all users in that tenant to receive `429`
- Rate limit response headers are present on every response (including successful ones)
- The `/api/auth/login` endpoint is rate-limited to 10 requests per minute per source IP; the 11th request returns `429`
- Service-to-service requests authenticated via service tokens are exempt from per-user and per-tenant rate limits
- Rate limit counters are stored in Redis; if Redis is unavailable, the middleware fails open (allows the request) and logs a warning
- Per-firm rate limit overrides can be configured (e.g., a large firm can be granted 5000 req/min)
- Rate limit state is not persisted to Postgres; it is ephemeral in Redis

### Dependencies

- Issue 2 (Subdomain Resolution Middleware)
- Issue 4 (JWT Access Token and Refresh Token System)

---

## Issue 9: Service-to-Service Authentication

### Description

Implement authentication for internal service calls between the API server, worker processes, and the AI sidecar. Service-to-service auth uses signed service tokens (JWTs with a distinct actor type) or mTLS to ensure that only authorized internal services can call platform APIs. This is separate from user authentication and carries its own identity and permission scope.

### Scope

- Service identity model: each internal service (worker, sidecar, integration adapter) has a registered service account with `id`, `name`, `service_key_hash`, and `allowed_permissions`
- Postgres schema for `service_accounts` table: `id` (UUID), `name`, `firm_id` (nullable for platform-global services), `key_hash`, `permissions` (JSONB array), `status` (`active`, `revoked`), `created_at`, `rotated_at`
- Service token format: JWT with claims `sub` (service account ID), `tid` (tenant ID or `*` for platform-global), `act: "service"`, `permissions` (array), `iat`, `exp`
- Service token expiration: short-lived (5 minutes), issued on demand by the service using its registered key
- Authentication middleware recognizes `act: "service"` and loads permissions from the token claims rather than from user role assignments
- Scoped permissions: a service account for the AI sidecar might only have `client.read`, `account.read`, `document.read` -- it cannot submit orders or manage users
- Key rotation: `POST /api/admin/service-accounts/:id/rotate-key` generates a new key and returns it once; old key remains valid for a configurable grace period (default 24 hours)
- Service account management endpoints (platform admin only): `POST /api/admin/service-accounts`, `GET /api/admin/service-accounts`, `DELETE /api/admin/service-accounts/:id`
- Correlation ID propagation: service-to-service calls must propagate `X-Request-Id` and `X-Correlation-Id` headers

### Acceptance Criteria

- A request with a valid service JWT (`act: "service"`) is authenticated and the service's permissions are loaded into the request context
- A service token with `act: "service"` and permissions `["client.read"]` passes `requirePermission("client.read")` but fails `requirePermission("order.submit")`
- A service token scoped to a specific `tid` can only access resources within that tenant
- A platform-global service token (`tid: "*"`) can access resources across tenants (used for cross-tenant operations like support tooling)
- Service token with an expired `exp` claim returns `401`
- After key rotation, both old and new keys are valid during the grace period; after the grace period, only the new key works
- A revoked service account's tokens are rejected even if not expired
- Service-to-service requests are exempt from per-user rate limiting (handled in Issue 8)
- All service-to-service calls carry `X-Request-Id` and `X-Correlation-Id` headers

### Dependencies

- Issue 4 (JWT Access Token and Refresh Token System)
- Issue 7 (Permission Enforcement Middleware)

---

## Issue 10: Support Impersonation with Audit

### Description

Implement a controlled impersonation mechanism that allows users with the `support.impersonate` permission to act on behalf of another user within a tenant. Impersonation sessions are time-limited, fully audited, and distinguishable from normal sessions in all logs and audit trails. This is a high-risk feature that requires explicit safeguards.

### Scope

- Postgres schema for `impersonation_sessions` table: `id` (UUID), `impersonator_user_id` (FK), `target_user_id` (FK), `firm_id` (FK), `reason` (text, required), `started_at`, `expires_at`, `ended_at`, `idempotency_key` (unique)
- `POST /api/support/impersonation` endpoint: requires `support.impersonate` permission; accepts `target_user_id`, `reason`, `duration_minutes` (max 60), and `idempotency_key`
- Returns an impersonation access token: JWT with `act: "impersonator"`, `sub` (impersonator's user ID), `imp` (target user ID), `tid` (tenant ID), `sid` (impersonation session ID), `roles` (target user's roles), `iat`, `exp`
- The impersonation token grants the permissions of the target user but is tagged as impersonated in all contexts
- `POST /api/support/impersonation/:id/end` endpoint: ends the impersonation session early
- `GET /api/support/impersonation` endpoint: list active and recent impersonation sessions for audit
- Impersonation sessions have a hard maximum duration of 60 minutes; tokens cannot be refreshed beyond this
- Impersonation restrictions: cannot impersonate another `support_impersonator` or `firm_admin`; cannot perform role management or impersonation actions while impersonating
- All actions taken during impersonation are tagged with both the impersonator ID and the target user ID in audit events
- Idempotency: starting an impersonation session with the same idempotency key returns the existing session

### Acceptance Criteria

- A user with `support.impersonate` permission can start an impersonation session and receives a token with `act: "impersonator"`
- A user without `support.impersonate` permission receives `403` when calling `POST /api/support/impersonation`
- The impersonation token carries the target user's roles and permissions but the `act` claim is `"impersonator"`, not `"user"`
- Attempting to impersonate a `firm_admin` returns `403` with a clear error message
- Attempting to impersonate another `support_impersonator` returns `403`
- Actions performed with an impersonation token cannot include `PUT /api/users/:id/roles` or `POST /api/support/impersonation` (escalation prevention)
- The impersonation session record is created in Postgres with `reason`, `impersonator_user_id`, and `target_user_id`
- An impersonation token that exceeds `expires_at` is rejected with `401`
- `GET /api/support/impersonation` returns a paginated list of impersonation sessions filterable by `impersonator_user_id`, `target_user_id`, and date range
- Duplicate `POST /api/support/impersonation` with the same `idempotency_key` returns the existing session rather than creating a new one
- An audit event of type `support.impersonation_started` is emitted when an impersonation session begins, and `support.impersonation_ended` when it ends (covered in Issue 12)

### Dependencies

- Issue 4 (JWT Access Token and Refresh Token System)
- Issue 6 (Role and Permission Model)
- Issue 7 (Permission Enforcement Middleware)
- Issue 12 (Audit Event Emission for Auth Actions)

---

## Issue 11: Session Management and Token Revocation

### Description

Implement session tracking and token revocation capabilities. Each user login creates a session record. Administrators and users can view active sessions and revoke them, which invalidates all associated refresh tokens. This issue also covers forced logout across all sessions and a token revocation check in the authentication middleware.

### Scope

- Postgres schema for `sessions` table: `id` (UUID), `user_id` (FK), `firm_id` (FK), `ip_address`, `user_agent`, `created_at`, `last_active_at`, `revoked_at`
- Session creation on successful login (after MFA if required)
- `GET /api/auth/sessions` endpoint: list active sessions for the current user
- `DELETE /api/auth/sessions/:id` endpoint: revoke a specific session (sets `revoked_at`, revokes all refresh tokens for that session)
- `POST /api/auth/sessions/revoke-all` endpoint: revoke all sessions for the current user except the current one
- `POST /api/admin/users/:id/revoke-sessions` endpoint: firm_admin can force-revoke all sessions for a target user
- Redis-based token revocation cache: when a session is revoked, the session ID is added to a Redis set with TTL matching the access token's maximum remaining lifetime; the auth middleware checks this set on every request
- Auth middleware enhancement: after JWT verification, check if the session ID (`sid` claim) is in the revocation set; if so, return `401`
- Update `last_active_at` on the session record periodically (not on every request; batched or sampled to avoid write amplification)
- Session limit per user: configurable maximum concurrent sessions (default: 10); oldest session is revoked when the limit is exceeded

### Acceptance Criteria

- `GET /api/auth/sessions` returns a list of the current user's active sessions with `id`, `ip_address`, `user_agent`, `created_at`, and `last_active_at`
- `DELETE /api/auth/sessions/:id` revokes the session; subsequent requests using tokens from that session return `401` within seconds (Redis propagation)
- `POST /api/auth/sessions/revoke-all` revokes all other sessions; only the current session remains active
- `POST /api/admin/users/:id/revoke-sessions` by a `firm_admin` revokes all sessions for the target user; the target user's next request returns `401`
- A non-`firm_admin` calling `POST /api/admin/users/:id/revoke-sessions` receives `403`
- After session revocation, the session ID appears in the Redis revocation set; the auth middleware rejects tokens with that session ID
- The Redis revocation entry TTL matches the access token expiration window (15 minutes) so it self-cleans
- When a user exceeds the concurrent session limit (10), the oldest session is automatically revoked
- Revoking a session also revokes all refresh tokens associated with that session in the `refresh_tokens` table
- Session revocation emits an audit event (covered in Issue 12)

### Dependencies

- Issue 4 (JWT Access Token and Refresh Token System)
- Issue 6 (Role and Permission Model)

---

## Issue 12: Audit Event Emission for Auth Actions

### Description

Implement audit event emission for all authentication and authorization actions in this epic. Audit events are append-only records persisted to Postgres and published to Kafka for downstream consumption. Every security-relevant action in the identity and access control domain must produce a structured, queryable audit trail.

### Scope

- Postgres schema for `audit_events` table: `id` (UUID), `firm_id`, `actor_id`, `actor_type` (`user`, `service`, `impersonator`), `action` (string), `resource_type`, `resource_id`, `metadata` (JSONB), `ip_address`, `user_agent`, `correlation_id`, `created_at`
- Append-only: no UPDATE or DELETE operations on the `audit_events` table
- Indexes: composite on `(firm_id, created_at)`, on `(firm_id, actor_id)`, on `(firm_id, action)`, on `(firm_id, resource_type, resource_id)`
- Audit event types for this epic:
  - `auth.login_success`
  - `auth.login_failure`
  - `auth.token_refresh`
  - `auth.logout`
  - `auth.mfa_enrolled`
  - `auth.mfa_challenge_success`
  - `auth.mfa_challenge_failure`
  - `auth.mfa_recovery_used`
  - `auth.session_revoked`
  - `auth.all_sessions_revoked`
  - `user.invited`
  - `user.registered`
  - `user.disabled`
  - `user.enabled`
  - `user.roles_changed`
  - `firm.created`
  - `firm.updated`
  - `firm.suspended`
  - `firm.activated`
  - `support.impersonation_started`
  - `support.impersonation_ended`
  - `service_account.created`
  - `service_account.key_rotated`
  - `service_account.revoked`
- Kafka topic: `platform.audit.auth` -- each audit event is published after Postgres persistence
- Audit emission middleware (position 8 in the pipeline): captures the response status and emits an audit event for the completed request on relevant routes
- Shared audit emitter utility: `emitAuditEvent({ firmId, actorId, actorType, action, resourceType, resourceId, metadata, ip, userAgent, correlationId })` usable from services and middleware
- `GET /api/audit/events` endpoint: queryable by `firm_id`, `actor_id`, `action`, `resource_type`, `resource_id`, and date range; requires `firm_admin` role; paginated
- Impersonation audit: when `actorType` is `impersonator`, `metadata` includes both `impersonatorId` and `targetUserId`

### Acceptance Criteria

- A successful login creates an `auth.login_success` audit event with `actor_id` set to the authenticated user, `ip_address`, and `user_agent`
- A failed login creates an `auth.login_failure` audit event with `metadata` containing the attempted email (not the password)
- MFA enrollment creates an `auth.mfa_enrolled` event; MFA challenge success and failure create their respective events
- Role changes create a `user.roles_changed` event with `metadata` containing `previous_roles` and `new_roles`
- Impersonation start creates a `support.impersonation_started` event with `metadata` containing `impersonator_id`, `target_user_id`, and `reason`
- Every audit event is written to Postgres and published to the `platform.audit.auth` Kafka topic
- `GET /api/audit/events?action=support.impersonation_started&from=2026-01-01&to=2026-03-31` returns all impersonation events in that range for the tenant
- Audit events cannot be modified or deleted via any API endpoint
- The audit emitter is callable from both middleware (for request-level events) and service methods (for business-level events like role changes)
- Audit event queries are performant with the defined indexes; a query scoped to `firm_id` and a 30-day date range returns within 200ms for up to 100,000 events

### Dependencies

- Issue 1 (Tenant Provisioning and Lifecycle)
- Issue 4 (JWT Access Token and Refresh Token System)
- Issue 10 (Support Impersonation with Audit)
- Issue 11 (Session Management and Token Revocation)

---

## Middleware Pipeline Summary

For reference, the full middleware ordering implemented across this epic:

| Position | Middleware | Issue |
|----------|-----------|-------|
| 1 | Request ID assignment | (shared infra) |
| 2 | Host/subdomain tenant resolution | Issue 2 |
| 3 | JWT authentication | Issue 4 |
| 4 | Actor and role resolution | Issue 6 |
| 5 | Permission enforcement | Issue 7 |
| 6 | Rate limiting | Issue 8 |
| 7 | Route handler | (per-domain) |
| 8 | Audit event emission | Issue 12 |

## Database Tables Introduced

| Table | Issue |
|-------|-------|
| `firms` | Issue 1 |
| `users` | Issue 3 |
| `invitations` | Issue 3 |
| `refresh_tokens` | Issue 4 |
| `mfa_factors` | Issue 5 |
| `mfa_recovery_codes` | Issue 5 |
| `roles` | Issue 6 |
| `permissions` | Issue 6 |
| `role_permissions` | Issue 6 |
| `user_role_assignments` | Issue 6 |
| `service_accounts` | Issue 9 |
| `impersonation_sessions` | Issue 10 |
| `sessions` | Issue 11 |
| `audit_events` | Issue 12 |
