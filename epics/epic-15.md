# Epic 15: Notifications and Operational Visibility

## Goal

Give users and operations teams timely, targeted visibility into important platform events, stuck work, SLA breaches, and exception states. Provide APIs that power operational dashboards for monitoring workflow health, transfer status, and exception queue depth across the platform.

## Context

The platform processes long-running workflows (onboarding, transfers, billing, trading) that involve multiple actors (advisors, operations staff, firm admins) and external dependencies (OMS, transfer rails, clearing). Without a structured notification system, critical events go unnoticed, exceptions pile up, and SLA commitments are missed silently.

This epic builds on the domain event model established in Epic 4 (External Service Integration Framework) and the workflow/case management chassis from Epic 3. Notifications are event-driven: the platform subscribes to domain events on Kafka, applies routing and deduplication rules, and delivers alerts to the correct actors through in-app and optional email channels.

## Architecture Notes

- Notifications are driven by Kafka consumers subscribing to domain event topics (onboarding, transfer, order, billing, workflow events).
- Notification state is persisted in Postgres (per-user inbox, read/unread status, deduplication keys, preferences).
- Redis is used for deduplication windows, SLA timer coordination, and caching operational dashboard aggregates.
- Notification routing evaluates actor assignments, roles, and tenant context to target the right recipients.
- Operational dashboard APIs are read models projected from workflow and event state, not ad hoc queries against transactional tables.

## Dependencies

- Epic 1: Tenant, Identity, and Access Control (tenant context, user identity, roles)
- Epic 3: Workflow and Case Management (workflow states, exception states, SLA timers)
- Epic 4: External Service Integration Framework (Kafka consumers, domain event contracts)
- Epic 8: Advisor Portal Experience (UI surface for notification inbox and dashboards)

---

## Issue 15.1: Notification Routing Engine

### Title

Build event-driven notification routing engine

### Description

Create the core notification routing engine that subscribes to domain events on Kafka and routes them to the appropriate notification channels and recipients. The engine consumes events from topic families (onboarding, transfer, order, billing, workflow), evaluates routing rules to determine which actors should be notified, and produces notification records in the notification store.

Routing rules map event types to notification templates and recipient resolution strategies. For example, `onboarding.case_exceptioned` routes to the assigned advisor and the operations queue; `transfer.failed` routes to the advisor who initiated the transfer and the operations team.

### Scope

- Kafka consumer group for notification-relevant domain events
- Routing rule configuration: event type to notification template mapping
- Recipient resolution interface (deferred to Issue 15.6 for actor-aware logic)
- Notification record creation in Postgres with tenant_id, user_id, event_type, payload, created_at, read status
- Notification template rendering (title, body, action URL, severity level)
- Correlation ID and workflow ID propagation from source events
- Idempotent event processing (consumer offset management, deduplication hook point)
- Support for severity levels: info, warning, critical

### Acceptance Criteria

- [ ] Kafka consumer subscribes to configured domain event topics and processes events reliably
- [ ] Each processed event produces zero or more notification records based on routing rules
- [ ] Notification records include tenant_id, recipient user_id, event_type, severity, title, body, action_url, correlation_id, workflow_id, and created_at
- [ ] Routing rules are configurable per event type without code changes (table-driven or config-driven)
- [ ] Consumer handles rebalances, retries, and poison messages without data loss
- [ ] Dead-letter handling for events that fail routing after retry exhaustion
- [ ] All notification creation is tenant-scoped

### Dependencies

- Epic 4 Kafka consumer framework and domain event contracts
- Epic 1 tenant context propagation

---

## Issue 15.2: In-App Notification Inbox

### Title

Build per-user in-app notification inbox with read/unread and pagination

### Description

Expose API endpoints that give each user a paginated inbox of their notifications. Each notification has a read/unread status. Users can mark individual notifications as read, mark all as read, and retrieve unread counts. The inbox is the primary delivery surface for all platform notifications.

### Scope

- `GET /notifications` -- list notifications for the authenticated user, paginated (cursor-based), filterable by severity, event_type, read status
- `GET /notifications/unread-count` -- return the count of unread notifications for the authenticated user
- `PATCH /notifications/:id/read` -- mark a single notification as read
- `POST /notifications/mark-all-read` -- mark all notifications as read for the authenticated user
- `GET /notifications/:id` -- retrieve a single notification with full payload
- Postgres schema: `notifications` table with id, tenant_id, user_id, event_type, severity, title, body, action_url, correlation_id, workflow_id, is_read, created_at, read_at
- Index on (tenant_id, user_id, is_read, created_at) for efficient inbox queries
- Tenant isolation enforced on all queries

### Acceptance Criteria

- [ ] Authenticated user can retrieve their notification inbox with cursor-based pagination
- [ ] Notifications are returned in reverse chronological order by default
- [ ] Filtering by severity, event_type, and read status works correctly
- [ ] Unread count endpoint returns accurate count
- [ ] Mark-as-read updates the notification and sets read_at timestamp
- [ ] Mark-all-read updates all unread notifications for the user in a single operation
- [ ] All endpoints enforce tenant isolation -- a user cannot see another tenant's notifications
- [ ] Pagination handles large notification volumes without performance degradation
- [ ] API responses include standard pagination metadata (cursor, has_more)

### Dependencies

- Issue 15.1 (notification records must exist)
- Epic 1 authentication and tenant middleware

---

## Issue 15.3: Workflow Alerts

### Title

Configure routing rules for workflow-lifecycle alerts

### Description

Define and implement the specific routing rules that produce notifications for key workflow transitions. These are the "happy path adjacent" alerts that keep advisors and operations staff informed about work that needs their attention or has reached a significant state change.

This issue focuses on the routing rule definitions and notification templates, not the routing engine itself (Issue 15.1) or the recipient targeting logic (Issue 15.6).

### Scope

- Onboarding workflow alerts:
  - Case submitted for review (`onboarding.case_submitted`) -- notify assigned reviewer
  - Case needs client action (`onboarding.pending_client_action`) -- notify assigned advisor
  - Case approved (`onboarding.case_approved`) -- notify assigned advisor
  - Case rejected (`onboarding.case_rejected`) -- notify assigned advisor
- Transfer workflow alerts:
  - Transfer submitted (`transfer.submitted`) -- notify initiating advisor
  - Transfer completed (`transfer.completed`) -- notify initiating advisor
  - Transfer failed (`transfer.failed`) -- notify initiating advisor and operations queue
  - Transfer reversed (`transfer.reversed`) -- notify initiating advisor and operations queue
- Billing workflow alerts:
  - Billing run ready for review (`billing.run_pending_review`) -- notify billing admin
  - Billing run posted (`billing.run_posted`) -- notify billing admin
  - Billing run failed (`billing.run_failed`) -- notify billing admin and operations queue
- Order workflow alerts:
  - Order rejected (`order.rejected`) -- notify submitting advisor
  - Order failed to settle (`order.failed_to_settle`) -- notify operations queue

### Acceptance Criteria

- [ ] Routing rules exist for all specified onboarding, transfer, billing, and order events
- [ ] Each rule maps to a notification template with appropriate severity level (info for completions, warning for failures, critical for rejections/reversals)
- [ ] Notification body includes contextual information: case ID, transfer ID, account identifier, amount where applicable
- [ ] Action URLs point to the relevant workflow detail view in the advisor portal
- [ ] Rules are additive -- new workflow alert rules can be added without modifying engine code
- [ ] All templates are tested with representative event payloads

### Dependencies

- Issue 15.1 (routing engine)
- Epic 3 workflow event definitions
- Epic 6, 7, 9, 12 domain event contracts

---

## Issue 15.4: Exception Alerts

### Title

Configure routing rules for exception and failure alerts

### Description

Define routing rules that produce high-priority notifications for exception conditions: stuck workflows, failed external service calls, reconciliation breaks, and any state that requires manual intervention. These alerts are operationally critical and must reach the right operations personnel promptly.

Exception alerts differ from workflow alerts (Issue 15.3) in that they represent abnormal conditions requiring investigation, not normal workflow progressions.

### Scope

- Stuck workflow detection alerts:
  - Workflow stuck in a non-terminal state beyond configured threshold (`workflow.stuck_detected`) -- notify operations queue and firm admin
- Failed external call alerts:
  - External service call exhausted retries (`integration.call_failed`) -- notify operations queue
  - Dead-letter event produced (`integration.dead_letter`) -- notify operations queue
- Reconciliation break alerts:
  - Cash reconciliation break detected (`reconciliation.break_detected`) -- notify operations queue and billing admin
  - Position reconciliation break detected (`reconciliation.position_break`) -- notify operations queue
- Onboarding exception alerts:
  - Case moved to exception state (`onboarding.case_exceptioned`) -- notify assigned advisor and operations queue
- Transfer exception alerts:
  - Transfer return received (`transfer.return_received`) -- notify operations queue and assigned advisor
- General exception alerts:
  - Manual intervention required on any workflow (`workflow.intervention_required`) -- notify operations queue

### Acceptance Criteria

- [ ] All specified exception event types have routing rules configured
- [ ] Exception alerts default to severity level "critical" or "warning" depending on operational impact
- [ ] Notification body includes: exception type, affected entity IDs, timestamp, brief description of what went wrong
- [ ] Action URLs point to the exception detail or workflow detail view
- [ ] Operations queue routing resolves to all users with the operations role within the tenant
- [ ] Exception alerts are never silently dropped -- failed routing attempts go to dead-letter with alerting
- [ ] Reconciliation break alerts include the break type, expected vs actual values where available

### Dependencies

- Issue 15.1 (routing engine)
- Issue 15.6 (actor-aware targeting for operations queue)
- Epic 3 exception state definitions
- Epic 4 dead-letter and retry exhaustion events

---

## Issue 15.5: Reminder Timer System

### Title

Build SLA-based reminder timer system for pending workflows

### Description

Implement a timer system that monitors workflow states and generates reminder notifications when work items remain in a pending state beyond configured SLA thresholds. Reminders escalate visibility for aging work without requiring the original event to be re-emitted.

The timer system runs as a periodic job (not event-driven) that scans workflow state and evaluates SLA rules. When a threshold is breached, it produces a reminder notification through the routing engine.

### Scope

- SLA rule configuration: workflow_type + state + threshold_duration -> reminder notification
- Example SLA rules:
  - Onboarding case in `pending_internal_review` for more than 24 hours -- remind assigned reviewer
  - Onboarding case in `pending_internal_review` for more than 48 hours -- escalate to firm admin
  - Transfer in `pending_external_review` for more than 72 hours -- remind operations queue
  - Billing run in `pending_review` for more than 48 hours -- remind billing admin
  - Any workflow in exception state for more than 24 hours -- remind operations queue
- Periodic job runner (cron-based or scheduled worker) that evaluates SLA rules against current workflow state
- Redis-based tracking of last reminder sent per workflow instance per SLA rule to avoid redundant reminders
- Escalation tiers: initial reminder to assigned actor, escalation reminder to supervisor/admin after further delay
- SLA rule storage in Postgres, configurable per tenant

### Acceptance Criteria

- [ ] Periodic job runs on a configurable interval (default: every 15 minutes)
- [ ] SLA rules are evaluated against current workflow state timestamps
- [ ] Reminder notifications are produced through the standard routing engine (Issue 15.1)
- [ ] Each workflow instance receives at most one reminder per SLA rule per evaluation window (no duplicate reminders on consecutive runs)
- [ ] Escalation tiers work: a longer SLA breach triggers a higher-severity reminder to a broader audience
- [ ] Reminders stop once the workflow advances past the monitored state
- [ ] SLA rules are tenant-configurable -- different firms can set different thresholds
- [ ] Timer state in Redis survives job restarts (TTL-based keys with sufficient duration)
- [ ] Job execution is idempotent and safe to run on overlapping schedules

### Dependencies

- Issue 15.1 (routing engine)
- Issue 15.6 (actor-aware targeting)
- Epic 3 workflow state and timestamp data

---

## Issue 15.6: Actor-Aware Notification Targeting

### Title

Implement actor-aware notification recipient resolution

### Description

Build the recipient resolution layer that determines which specific users should receive a notification based on the event context, actor assignments, and role definitions. This replaces the placeholder recipient resolution interface from Issue 15.1 with real logic.

Targeting must consider: the actor assigned to the workflow (e.g., assigned advisor, assigned reviewer), role-based groups (e.g., all operations staff in the tenant, billing admins), and firm admin escalation paths.

### Scope

- Recipient resolver interface with pluggable strategies:
  - `assigned_actor`: resolve to the user assigned to the workflow or case
  - `role_group`: resolve to all users with a given role within the tenant (e.g., operations, billing_admin, firm_admin)
  - `initiating_actor`: resolve to the user who initiated the action (e.g., the advisor who submitted a transfer)
  - `escalation_chain`: resolve through assignment -> role group -> firm admin
- Integration with Epic 1 role and permission model to look up users by role within a tenant
- Integration with Epic 3 workflow/case assignment data to resolve assigned actors
- Routing rules reference resolver strategies by name (e.g., `"recipients": ["assigned_actor", "role_group:operations"]`)
- Fallback behavior: if no assigned actor is found, fall back to role group; if role group is empty, fall back to firm admin
- Tenant-scoped resolution -- never cross tenant boundaries

### Acceptance Criteria

- [ ] Assigned actor resolution correctly identifies the user assigned to a workflow or case
- [ ] Role group resolution returns all active users with the specified role in the tenant
- [ ] Initiating actor resolution identifies the user who triggered the originating action
- [ ] Escalation chain correctly falls back through the defined hierarchy
- [ ] Multiple resolver strategies can be composed for a single routing rule (union of recipients, deduplicated)
- [ ] Resolution never returns users from a different tenant
- [ ] Resolution handles edge cases: no assigned actor, empty role group, deactivated users
- [ ] Deactivated or suspended users are excluded from recipient lists

### Dependencies

- Issue 15.1 (routing engine recipient interface)
- Epic 1 user and role model
- Epic 3 workflow assignment data

---

## Issue 15.7: Notification Deduplication

### Title

Implement notification deduplication to prevent redundant alerts

### Description

Build a deduplication layer that prevents the same logical alert from being sent repeatedly for the same event or condition. Deduplication operates at two levels: event-level (the same Kafka event processed twice due to consumer retries) and semantic-level (the same condition producing repeated alerts, such as a stuck workflow generating a new alert every time the timer job runs).

### Scope

- Event-level deduplication:
  - Each domain event carries an event_id; notification creation is idempotent on (event_id, recipient_user_id)
  - Postgres unique constraint or upsert logic on a deduplication key
- Semantic-level deduplication:
  - Deduplication key composed of: event_type + entity_id + recipient_user_id + dedup_window
  - Configurable deduplication window per event type (e.g., "do not re-alert on transfer.failed for the same transfer_id within 4 hours")
  - Redis-based dedup window tracking with TTL keys: `dedup:{tenant_id}:{event_type}:{entity_id}:{user_id}` with TTL equal to dedup window
- Dedup window configuration stored alongside routing rules
- Bypass mechanism for escalation reminders (escalation to a new tier should not be suppressed by lower-tier dedup)

### Acceptance Criteria

- [ ] Duplicate Kafka events (same event_id) do not produce duplicate notification records
- [ ] Semantic deduplication suppresses repeated alerts for the same entity within the configured window
- [ ] Deduplication windows are configurable per event type
- [ ] Escalation-tier reminders bypass deduplication for the same entity (a 48h escalation is not suppressed by a 24h reminder dedup window)
- [ ] Redis TTL keys expire correctly, allowing re-notification after the window elapses
- [ ] Deduplication metrics are observable: count of suppressed notifications per event type
- [ ] Deduplication does not suppress notifications for different entities of the same event type

### Dependencies

- Issue 15.1 (routing engine)
- Issue 15.5 (reminder timer integration)
- Redis infrastructure (Epic 4)

---

## Issue 15.8: Operational Dashboard APIs

### Title

Build operational dashboard read-model APIs

### Description

Expose API endpoints that provide aggregated operational metrics for the advisor portal dashboard and operations workspace. These APIs return counts and distributions for pending work, stuck workflows, exception queues, and transfer statuses. They are read models projected from workflow and event state, not live transactional queries.

### Scope

- `GET /operations/dashboard` -- composite endpoint returning key operational metrics for the tenant:
  - Pending onboarding cases count (by status: pending_review, pending_client_action, exceptioned)
  - Pending transfer count (by status: submitted, in_transit, pending_external_review, failed)
  - Stuck workflow count (workflows exceeding SLA thresholds)
  - Exception queue depth (workflows in exception state)
  - Transfer status distribution (count per status)
  - Unresolved reconciliation breaks count
- `GET /operations/exceptions` -- paginated list of active exceptions across workflow types, filterable by type and severity
- `GET /operations/stuck-workflows` -- paginated list of workflows exceeding SLA thresholds, sortable by age
- `GET /operations/transfers/status-distribution` -- transfer counts grouped by status and type
- Redis caching layer for dashboard aggregates with short TTL (30-60 seconds)
- Materialized views or query projections in Postgres for efficient aggregation
- All endpoints tenant-scoped and restricted to operations, firm_admin, or advisor roles as appropriate

### Acceptance Criteria

- [ ] Dashboard endpoint returns all specified metrics in a single response
- [ ] Metrics are accurate within the caching TTL window (eventual consistency is acceptable)
- [ ] Exception list is paginated and filterable by workflow type and severity
- [ ] Stuck workflow list correctly identifies workflows beyond SLA thresholds and includes time-in-state
- [ ] Transfer distribution accurately reflects current transfer states
- [ ] All endpoints enforce tenant isolation
- [ ] Role-based access: operations and firm_admin see full dashboard; advisors see their assigned work only
- [ ] Response times remain under 500ms even with large datasets (caching and projections)
- [ ] Dashboard data does not require direct queries against transactional workflow tables in the hot path

### Dependencies

- Epic 3 workflow state data
- Epic 7 transfer state data
- Epic 1 role-based access control
- Issue 15.5 SLA threshold definitions (for stuck workflow detection)

---

## Issue 15.9: Email Notification Integration

### Title

Add email delivery channel for critical notifications

### Description

Integrate an email delivery channel so that critical and high-severity notifications can optionally be sent via email in addition to the in-app inbox. Email delivery is a secondary channel -- the in-app notification is always created first, and email is dispatched asynchronously based on notification severity and user preferences (Issue 15.10).

### Scope

- Email dispatch service that accepts notification payloads and sends formatted emails
- Integration with an email provider (SendGrid, SES, or similar) via an adapter pattern
- Email templates for notification categories: workflow alerts, exception alerts, SLA reminders
- Email dispatch triggered asynchronously after notification record creation (Kafka event or internal queue)
- Email includes: notification title, body, action URL as a clickable link, tenant branding
- Rate limiting: maximum emails per user per hour to prevent flood
- Unsubscribe link in every email (ties into Issue 15.10 preferences)
- Email delivery status tracking (sent, bounced, failed) in Postgres
- Tenant-level toggle to enable/disable email notifications

### Acceptance Criteria

- [ ] Critical-severity notifications trigger email dispatch to the recipient
- [ ] Email content matches the in-app notification content with appropriate formatting
- [ ] Email includes a direct action link to the relevant platform page
- [ ] Rate limiting prevents more than a configurable number of emails per user per hour (default: 20)
- [ ] Email delivery failures are logged and do not block in-app notification delivery
- [ ] Bounced email addresses are tracked and flagged
- [ ] Tenant-level toggle can disable all email notifications for a firm
- [ ] Email adapter is behind an interface so the provider can be swapped without code changes
- [ ] Email templates are tenant-brandable (logo, firm name, colors)
- [ ] Unsubscribe link correctly updates user preferences

### Dependencies

- Issue 15.1 (notification records)
- Issue 15.10 (user preferences for email opt-in/out)
- Epic 1 user email addresses
- External email provider account and API credentials

---

## Issue 15.10: Notification Preferences

### Title

Build per-user notification preferences for event types and channels

### Description

Allow users to configure which notification event types they want to receive and through which channels (in-app, email). Preferences are per-user within a tenant. The routing engine (Issue 15.1) and email integration (Issue 15.9) consult preferences before creating or dispatching notifications.

Some notification types are mandatory and cannot be suppressed (e.g., critical security alerts, exception alerts for assigned workflows). Preferences only govern optional and informational notifications.

### Scope

- `GET /notifications/preferences` -- retrieve the authenticated user's notification preferences
- `PUT /notifications/preferences` -- update the authenticated user's notification preferences
- Preferences schema:
  - Per event_type category: enabled/disabled for in-app, enabled/disabled for email
  - Categories: onboarding_updates, transfer_updates, billing_updates, order_updates, sla_reminders, exception_alerts, operational_summaries
  - Default preferences applied when a user has not explicitly configured preferences
- Mandatory notification list: event types that cannot be disabled (critical exceptions, security events)
- Postgres schema: `notification_preferences` table with user_id, tenant_id, category, in_app_enabled, email_enabled
- Routing engine integration: before creating a notification, check if the recipient has disabled that category
- Email integration: before dispatching email, check if the recipient has email enabled for that category
- Firm-admin ability to set default preferences for the tenant

### Acceptance Criteria

- [ ] Users can retrieve their current notification preferences
- [ ] Users can update preferences per notification category and per channel
- [ ] Default preferences are applied for users who have not configured any preferences
- [ ] Mandatory notifications are always delivered regardless of user preferences
- [ ] Routing engine respects in-app preferences -- disabled categories do not produce notification records (except mandatory)
- [ ] Email integration respects email preferences -- disabled categories do not trigger email dispatch
- [ ] Firm admins can set tenant-level default preferences that apply to new users
- [ ] Preference changes take effect immediately for subsequent notifications
- [ ] API validates that mandatory categories cannot be set to disabled
- [ ] Preferences are tenant-scoped -- one user's preferences do not affect another tenant

### Dependencies

- Issue 15.1 (routing engine preference check integration point)
- Issue 15.9 (email channel preference check)
- Epic 1 user identity and role model

---

## Summary

| Issue | Title | Priority | Depends On |
|-------|-------|----------|------------|
| 15.1 | Notification Routing Engine | P0 | Epic 1, Epic 4 |
| 15.2 | In-App Notification Inbox | P0 | 15.1, Epic 1 |
| 15.3 | Workflow Alerts | P0 | 15.1, Epic 3/6/7/9/12 |
| 15.4 | Exception Alerts | P0 | 15.1, 15.6, Epic 3/4 |
| 15.5 | Reminder Timer System | P1 | 15.1, 15.6, Epic 3 |
| 15.6 | Actor-Aware Notification Targeting | P0 | 15.1, Epic 1, Epic 3 |
| 15.7 | Notification Deduplication | P1 | 15.1, 15.5 |
| 15.8 | Operational Dashboard APIs | P1 | Epic 1/3/7, 15.5 |
| 15.9 | Email Notification Integration | P2 | 15.1, 15.10, Epic 1 |
| 15.10 | Notification Preferences | P1 | 15.1, 15.9, Epic 1 |

### Recommended Implementation Order

1. Issue 15.1 -- Notification Routing Engine (foundation)
2. Issue 15.6 -- Actor-Aware Notification Targeting (required by routing rules)
3. Issue 15.2 -- In-App Notification Inbox (delivery surface)
4. Issue 15.3 -- Workflow Alerts (first set of routing rules)
5. Issue 15.4 -- Exception Alerts (operational-critical routing rules)
6. Issue 15.7 -- Notification Deduplication (prevents noise as volume grows)
7. Issue 15.5 -- Reminder Timer System (SLA enforcement)
8. Issue 15.8 -- Operational Dashboard APIs (visibility layer)
9. Issue 15.10 -- Notification Preferences (user control)
10. Issue 15.9 -- Email Notification Integration (secondary channel)
