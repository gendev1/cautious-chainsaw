# Epic 2: Client, Household, and Account Registry

## Goal

Create the canonical business graph for households, clients, legal parties, account registrations, and their associated relationships. This epic establishes the core domain model that virtually every downstream epic depends on: onboarding, transfers, trading, billing, reporting, and the advisor portal.

## Dependencies

- Epic 1: Tenant, Identity, and Access Control (tenant scoping, permission enforcement, audit emission)

## Architecture Context

- **Store of record:** Postgres
- **Module boundaries:** `modules/households/`, `modules/clients/`, `modules/accounts/`
- **Validation:** Zod schemas at the HTTP boundary; typed service inputs internally
- **Events:** Kafka domain events for downstream consumers (`household.created`, `account.status_changed`, etc.)
- **Read models:** Optional MongoDB denormalized household dashboard projection
- **Sensitive data:** Application-level encryption for SSN/TIN; PII masking in API responses and logs

---

## Issue 1: Household CRUD and Lifecycle

### Title

Implement household entity with CRUD operations and lifecycle management

### Description

Households are the top-level organizational unit for grouping clients and accounts. Every client belongs to exactly one household. Households are tenant-scoped and advisor-associated. The household entity owns the relationship graph downward to clients, accounts, beneficiaries, and trusted contacts.

### Scope

- Postgres table `households` with columns: `id` (UUID), `tenant_id`, `name`, `status`, `primary_advisor_id`, `service_team_json` (JSONB), `notes`, `created_at`, `updated_at`, `created_by`
- Status lifecycle: `active` -> `inactive` -> `closed`
- Zod schemas for create, update, and response payloads
- Repository layer with tenant-scoped queries
- Service layer enforcing business rules (cannot close household with active accounts)
- REST endpoints:
  - `POST /api/households`
  - `GET /api/households`
  - `GET /api/households/:id`
  - `PATCH /api/households/:id`
  - `POST /api/households/:id/deactivate`
  - `POST /api/households/:id/reactivate`
  - `POST /api/households/:id/close`
- Kafka events: `household.created`, `household.updated`, `household.status_changed`
- Audit event emission on every mutating operation

### Acceptance Criteria

- [ ] Creating a household with a valid name and primary advisor ID returns `201` with the household record including a UUID `id`
- [ ] Creating a household without a `name` returns `400` with error code `VALIDATION_ERROR`
- [ ] All household queries are scoped to the authenticated tenant; a request from tenant A never returns tenant B households
- [ ] `GET /api/households` supports pagination (`limit`, `offset`) and filtering by `status` and `primary_advisor_id`
- [ ] Deactivating a household transitions status from `active` to `inactive` and emits `household.status_changed` to Kafka
- [ ] Closing a household with one or more accounts in `active` or `restricted` status returns `400` with error code `INVALID_WORKFLOW_STATE`
- [ ] Closing a household with only `closed` or zero accounts transitions status to `closed`
- [ ] Reactivating a household transitions status from `inactive` to `active`; attempting to reactivate a `closed` household returns `400`
- [ ] Every create, update, deactivate, reactivate, and close operation writes an audit event with actor, tenant, resource type, resource ID, action, and timestamp
- [ ] The `created_by` field is populated from the authenticated actor and is immutable after creation

### Dependencies

- Epic 1: tenant resolution middleware, permission enforcement, audit emission infrastructure

---

## Issue 2: ClientPerson Records (Individuals)

### Title

Implement ClientPerson entity for individual client records

### Description

ClientPerson represents a natural person (individual) associated with a household. This entity captures legal identity, contact information, demographic data, employment details, and regulatory classifications. ClientPerson records are referenced by account registrations, beneficiary designations, trusted contact records, and onboarding cases.

### Scope

- Postgres table `client_persons` with columns: `id` (UUID), `tenant_id`, `household_id`, `first_name`, `middle_name`, `last_name`, `suffix`, `date_of_birth`, `ssn_encrypted`, `ssn_last_four`, `citizenship_country`, `residency_country`, `tax_id_type`, `email`, `phone_primary`, `phone_secondary`, `mailing_address_json` (JSONB), `residential_address_json` (JSONB), `employment_status`, `employer_name`, `occupation`, `is_control_person`, `is_politically_exposed`, `regulatory_disclosures_json` (JSONB), `status`, `created_at`, `updated_at`, `created_by`
- Foreign key to `households`
- Status lifecycle: `draft` -> `active` -> `inactive`
- Zod schemas for create, update, and response payloads (response schema must exclude `ssn_encrypted`)
- REST endpoints:
  - `POST /api/clients/persons`
  - `GET /api/clients/persons`
  - `GET /api/clients/persons/:id`
  - `PATCH /api/clients/persons/:id`
- Kafka events: `client_person.created`, `client_person.updated`

### Acceptance Criteria

- [ ] Creating a client person with valid required fields (`first_name`, `last_name`, `date_of_birth`, `household_id`) returns `201`
- [ ] The `household_id` must reference an existing household within the same tenant; a nonexistent or cross-tenant household ID returns `400`
- [ ] The `ssn_encrypted` field is never included in any API response; only `ssn_last_four` is returned
- [ ] The `date_of_birth` field accepts ISO 8601 date format and rejects future dates
- [ ] `email` field is validated as a well-formed email address; `phone_primary` accepts E.164 format
- [ ] `GET /api/clients/persons` supports filtering by `household_id`, `status`, and text search on name fields
- [ ] Updating a client person with `is_politically_exposed: true` emits an audit event with elevated classification
- [ ] A client person cannot be created in a household with status `closed`
- [ ] The response presenter never leaks database column names for encrypted fields

### Dependencies

- Issue 1 (Household CRUD)
- Issue 11 (Sensitive data handling -- SSN encryption implementation)

---

## Issue 3: ClientEntity Records (Trusts, Corporations, Partnerships)

### Title

Implement ClientEntity for trusts, corporations, partnerships, and other legal entities

### Description

ClientEntity represents a non-natural-person legal entity that can own accounts. Entity types include revocable trusts, irrevocable trusts, C-corporations, S-corporations, LLCs, general partnerships, limited partnerships, non-profit organizations, and estate accounts. Each entity type has distinct required fields and regulatory characteristics. Entities are linked to a household and reference one or more ClientPerson records as authorized individuals.

### Scope

- Postgres table `client_entities` with columns: `id` (UUID), `tenant_id`, `household_id`, `entity_type` (enum), `legal_name`, `dba_name`, `tin_encrypted`, `tin_last_four`, `formation_date`, `formation_state`, `formation_country`, `tax_classification`, `mailing_address_json` (JSONB), `principal_address_json` (JSONB), `governing_document_type`, `status`, `created_at`, `updated_at`, `created_by`
- Entity type enum: `revocable_trust`, `irrevocable_trust`, `c_corporation`, `s_corporation`, `llc`, `general_partnership`, `limited_partnership`, `non_profit`, `estate`, `other`
- Junction table `client_entity_roles` linking `client_entity_id` to `client_person_id` with `role` (enum: `trustee`, `grantor`, `beneficiary`, `officer`, `director`, `partner`, `manager`, `authorized_representative`)
- REST endpoints:
  - `POST /api/clients/entities`
  - `GET /api/clients/entities`
  - `GET /api/clients/entities/:id`
  - `PATCH /api/clients/entities/:id`
  - `POST /api/clients/entities/:id/roles`
  - `DELETE /api/clients/entities/:id/roles/:roleId`
- Kafka events: `client_entity.created`, `client_entity.updated`

### Acceptance Criteria

- [ ] Creating a client entity with `entity_type: "revocable_trust"` requires `formation_date` and `formation_state`; missing either returns `400` with field-level errors
- [ ] Creating a client entity with `entity_type: "c_corporation"` requires `formation_state` and `tax_classification`
- [ ] `tin_encrypted` is never returned in API responses; only `tin_last_four` is exposed
- [ ] Adding a role to an entity validates that the referenced `client_person_id` belongs to the same household
- [ ] A trust entity requires at least one person with role `trustee` before its status can transition from `draft` to `active`
- [ ] `GET /api/clients/entities` supports filtering by `household_id`, `entity_type`, and `status`
- [ ] Deleting an entity role that is the last `trustee` on a trust returns `400` with a descriptive error
- [ ] Entity type enum is validated at the Zod schema level; unknown types return `400`
- [ ] The `governing_document_type` field accepts values like `trust_agreement`, `articles_of_incorporation`, `partnership_agreement`, `operating_agreement`

### Dependencies

- Issue 1 (Household CRUD)
- Issue 2 (ClientPerson -- for entity role references)
- Issue 11 (Sensitive data handling -- TIN encryption)

---

## Issue 4: Advisor-Client Relationship Management

### Title

Implement advisor-client and advisor-household relationship assignments

### Description

Advisors within a firm need explicit, queryable relationships to households and individual clients. A household has a primary advisor and may have additional team members. Relationships determine data visibility, permission scoping, and operational assignment. Relationship changes must be audited for compliance.

### Scope

- Postgres table `advisor_relationships` with columns: `id` (UUID), `tenant_id`, `advisor_user_id`, `household_id`, `client_person_id` (nullable), `relationship_type` (enum: `primary_advisor`, `secondary_advisor`, `service_associate`, `operations_support`), `effective_date`, `end_date` (nullable), `status`, `created_at`, `updated_at`, `created_by`
- Unique constraint: one `primary_advisor` per household at a time
- REST endpoints:
  - `POST /api/advisor-relationships`
  - `GET /api/advisor-relationships`
  - `PATCH /api/advisor-relationships/:id`
  - `POST /api/advisor-relationships/:id/end`
- Kafka events: `advisor_relationship.created`, `advisor_relationship.ended`

### Acceptance Criteria

- [ ] Assigning a `primary_advisor` to a household that already has an active primary advisor returns `409` with error code `IDEMPOTENCY_CONFLICT` unless the existing relationship is ended first
- [ ] Ending an advisor relationship sets `end_date` to the current timestamp and transitions status to `ended`
- [ ] `GET /api/advisor-relationships` supports filtering by `advisor_user_id`, `household_id`, `relationship_type`, and `status`
- [ ] The `advisor_user_id` must reference an active user within the same tenant with a role that includes `client.read` permission
- [ ] Creating or ending a relationship emits an audit event capturing the previous and new state
- [ ] A household cannot have zero `primary_advisor` relationships; ending the last primary advisor returns `400` unless a replacement is provided in the same request
- [ ] Querying households as an advisor with role-scoped visibility returns only households where the advisor has an active relationship (when permission model enforces advisor-scoping)
- [ ] `effective_date` defaults to the current date if not provided; `effective_date` in the future is allowed for scheduled transitions

### Dependencies

- Issue 1 (Household CRUD)
- Epic 1 (User records, role/permission model)

---

## Issue 5: Account Registration Types and Lifecycle

### Title

Implement account registration model supporting 30+ account types with type-specific validation

### Description

Account registrations define the legal structure under which an account is held. The platform must support the full range of retail and institutional account types that an RIA custody platform handles. Each registration type carries distinct ownership rules, required fields, tax treatment, and regulatory constraints. Registration type and account are separate concepts: one client or entity may have many accounts, and the registration defines the legal wrapper.

### Scope

- Postgres table `account_registrations` with columns: `id` (UUID), `tenant_id`, `registration_type` (enum), `account_id`, `primary_owner_person_id` (nullable), `primary_owner_entity_id` (nullable), `joint_owner_person_id` (nullable), `custodian_person_id` (nullable), `minor_person_id` (nullable), `ira_subtype` (nullable), `trust_entity_id` (nullable), `tax_status`, `state_of_jurisdiction` (nullable), `registration_details_json` (JSONB for type-specific overflow), `created_at`, `updated_at`
- Postgres table `accounts` with columns: `id` (UUID), `tenant_id`, `household_id`, `account_number` (unique per tenant), `display_name`, `registration_id`, `status`, `opened_date`, `closed_date`, `restricted_reason`, `custodian_account_id` (nullable), `created_at`, `updated_at`, `created_by`
- Registration type enum (minimum set):
  - Individual: `individual`
  - Joint: `joint_tenants_wros`, `joint_tenants_in_common`, `joint_community_property`
  - IRA variants: `traditional_ira`, `roth_ira`, `sep_ira`, `simple_ira`, `inherited_traditional_ira`, `inherited_roth_ira`, `rollover_ira`
  - Retirement: `individual_401k`, `roth_401k`, `403b`, `457b`
  - Education: `529_plan`, `coverdell_esa`
  - Custodial: `ugma`, `utma`
  - Trust: `revocable_trust`, `irrevocable_trust`
  - Entity: `corporate`, `llc`, `partnership`, `non_profit`
  - Specialty: `estate`, `conservatorship`, `hsa`
  - Other: `toa_receiving` (transfer-on-death), `donor_advised_fund`
- Account status lifecycle: `draft` -> `pending_approval` -> `active` -> `restricted` -> `closed`
- REST endpoints:
  - `POST /api/accounts`
  - `GET /api/accounts`
  - `GET /api/accounts/:id`
  - `PATCH /api/accounts/:id`
  - `POST /api/accounts/:id/submit` (draft -> pending_approval)
  - `POST /api/accounts/:id/activate` (pending_approval -> active)
  - `POST /api/accounts/:id/restrict` (active -> restricted)
  - `POST /api/accounts/:id/unrestrict` (restricted -> active)
  - `POST /api/accounts/:id/close` (active or restricted -> closed)
- Kafka events: `account.created`, `account.submitted`, `account.activated`, `account.restricted`, `account.closed`

### Acceptance Criteria

- [ ] Creating an account with `registration_type: "individual"` requires `primary_owner_person_id`; omitting it returns `400`
- [ ] Creating an account with `registration_type: "joint_tenants_wros"` requires both `primary_owner_person_id` and `joint_owner_person_id`; omitting either returns `400`
- [ ] Creating an account with `registration_type: "traditional_ira"` requires `primary_owner_person_id` and sets `tax_status` to `tax_deferred` automatically
- [ ] Creating an account with `registration_type: "roth_ira"` sets `tax_status` to `tax_exempt`
- [ ] Creating an account with `registration_type: "ugma"` or `"utma"` requires both `custodian_person_id` and `minor_person_id`
- [ ] Creating an account with a trust registration type requires `trust_entity_id` referencing a valid ClientEntity of matching type
- [ ] Creating an account with a corporate/LLC/partnership registration type requires `primary_owner_entity_id`
- [ ] Account numbers are generated as unique-per-tenant identifiers and are immutable after creation
- [ ] `GET /api/accounts` supports filtering by `household_id`, `status`, `registration_type`, and `primary_owner_person_id`
- [ ] All referenced person and entity IDs must belong to the same household; cross-household references return `400`
- [ ] Each registration type enum value is validated at the Zod layer; unsupported types return `400`
- [ ] The `registration_details_json` JSONB column stores type-specific fields that do not fit the common schema (e.g., `plan_name` for 401k, `beneficiary_of_decedent` for inherited IRA)

### Dependencies

- Issue 1 (Household CRUD)
- Issue 2 (ClientPerson)
- Issue 3 (ClientEntity)

---

## Issue 6: Account Status Lifecycle

### Title

Implement account status state machine with transition validation and audit trail

### Description

Account status controls what operations are permitted on an account. Status transitions follow a strict state machine: `draft` -> `pending_approval` -> `active` -> `restricted` -> `closed`, with specific allowed transitions and guard conditions. Invalid transitions must be rejected. Every transition must be recorded in an immutable history table for audit and compliance.

### Scope

- Postgres table `account_status_transitions` with columns: `id` (UUID), `account_id`, `from_status`, `to_status`, `reason`, `initiated_by`, `approved_by` (nullable), `metadata_json` (JSONB), `created_at`
- State machine enforcement in the account service layer (not in the database)
- Allowed transitions:
  - `draft` -> `pending_approval`
  - `pending_approval` -> `active`
  - `pending_approval` -> `draft` (rejection/return to draft)
  - `active` -> `restricted`
  - `restricted` -> `active`
  - `active` -> `closed`
  - `restricted` -> `closed`
- Restriction reasons enum: `regulatory_hold`, `legal_hold`, `fraud_investigation`, `deceased_owner`, `aml_review`, `operations_hold`, `voluntary`
- Guard conditions: closing an account requires zero open positions and zero pending transfers (enforced at service layer; stubbed initially)

### Acceptance Criteria

- [ ] Attempting `draft` -> `closed` directly returns `400` with error code `INVALID_WORKFLOW_STATE` and a message listing valid transitions
- [ ] Attempting `closed` -> `active` returns `400`; closed is a terminal state
- [ ] Transitioning `active` -> `restricted` requires a `reason` from the restriction reasons enum; omitting it returns `400`
- [ ] Every status transition creates a row in `account_status_transitions` with the actor, reason, and timestamps
- [ ] `GET /api/accounts/:id/status-history` returns the ordered list of transitions for the account
- [ ] The `approved_by` field is populated when the transition requires approval (e.g., `pending_approval` -> `active`)
- [ ] The state machine rejects concurrent conflicting transitions: if two requests attempt to transition the same account simultaneously, exactly one succeeds and the other receives `409`
- [ ] Kafka event `account.status_changed` is emitted for every successful transition, including `from_status`, `to_status`, and `reason`
- [ ] Accounts in `restricted` status reject order intent creation and transfer submission at the service layer (can be stubbed until those epics are built, but the check must exist)

### Dependencies

- Issue 5 (Account registration and account table)
- Epic 1 (Permission enforcement for approval actions)

---

## Issue 7: Beneficiary Management

### Title

Implement beneficiary designation management with primary/contingent classification and percentage validation

### Description

Beneficiaries are designated recipients of account assets upon the account holder's death. Beneficiary designations are required for IRA, retirement, and TOD accounts, and optional for others. Each account may have multiple primary and contingent beneficiaries. Primary beneficiary percentages must sum to exactly 100%. Contingent beneficiary percentages must independently sum to exactly 100% when any contingent beneficiaries exist. Beneficiaries can be persons (ClientPerson), entities (ClientEntity), trusts, estates, or charities.

### Scope

- Postgres table `beneficiaries` with columns: `id` (UUID), `tenant_id`, `account_id`, `designation_type` (enum: `primary`, `contingent`), `beneficiary_type` (enum: `person`, `entity`, `trust`, `estate`, `charity`), `client_person_id` (nullable), `client_entity_id` (nullable), `external_name` (nullable, for non-client beneficiaries), `relationship_to_owner`, `percentage` (decimal, 2 places), `per_stirpes` (boolean), `date_of_birth` (nullable), `ssn_encrypted` (nullable), `ssn_last_four` (nullable), `address_json` (JSONB, nullable), `status`, `created_at`, `updated_at`
- REST endpoints:
  - `POST /api/accounts/:accountId/beneficiaries`
  - `GET /api/accounts/:accountId/beneficiaries`
  - `PATCH /api/accounts/:accountId/beneficiaries/:id`
  - `DELETE /api/accounts/:accountId/beneficiaries/:id`
  - `POST /api/accounts/:accountId/beneficiaries/validate`
- Kafka event: `beneficiary.designation_changed`

### Acceptance Criteria

- [ ] Adding a primary beneficiary with `percentage: 50.00` when existing primary beneficiaries total `60.00` succeeds (total is 110%, which is invalid but individual adds are allowed; validation endpoint catches it)
- [ ] `POST /api/accounts/:accountId/beneficiaries/validate` returns `{ valid: false, errors: ["primary beneficiary percentages sum to 110.00, must equal 100.00"] }` when primary percentages do not sum to 100.00
- [ ] `POST /api/accounts/:accountId/beneficiaries/validate` returns `{ valid: true }` when primary beneficiaries sum to exactly 100.00 and contingent beneficiaries (if any) sum to exactly 100.00
- [ ] Creating a beneficiary with `beneficiary_type: "person"` and a valid `client_person_id` auto-populates name and relationship from the ClientPerson record
- [ ] Creating a beneficiary with `beneficiary_type: "charity"` requires `external_name` and does not require `client_person_id`
- [ ] `percentage` field accepts values from `0.01` to `100.00` with exactly two decimal places; `0` or `100.01` returns `400`
- [ ] `per_stirpes` flag defaults to `false` and is included in all response payloads
- [ ] Deleting the last primary beneficiary on a traditional IRA account emits a warning in the response metadata (not a blocking error, since designation may be in progress)
- [ ] `ssn_encrypted` for non-client beneficiaries is never returned in API responses; only `ssn_last_four`
- [ ] Beneficiary changes emit `beneficiary.designation_changed` with account ID and designation summary

### Dependencies

- Issue 5 (Account registration -- to determine which accounts require beneficiaries)
- Issue 2 (ClientPerson)
- Issue 3 (ClientEntity)
- Issue 11 (Sensitive data handling)

---

## Issue 8: Trusted Contact Management

### Title

Implement trusted contact management compliant with SEC Rule 17a-3

### Description

SEC Rule 17a-3(a)(17) requires broker-dealers and RIA custodians to make reasonable efforts to obtain the name and contact information of a trusted contact person for each customer account. The trusted contact is someone the firm may reach out to if there are concerns about the account holder's wellbeing, potential financial exploitation, or to confirm contact information. Trusted contacts are not account holders, authorized signers, or beneficiaries -- they serve a protective function only.

### Scope

- Postgres table `trusted_contacts` with columns: `id` (UUID), `tenant_id`, `account_id`, `client_person_id` (nullable, if the trusted contact is an existing client), `first_name`, `last_name`, `relationship_to_owner`, `phone`, `email`, `mailing_address_json` (JSONB), `date_designated`, `date_removed` (nullable), `removal_reason` (nullable), `status` (enum: `active`, `removed`), `created_at`, `updated_at`, `created_by`
- REST endpoints:
  - `POST /api/accounts/:accountId/trusted-contacts`
  - `GET /api/accounts/:accountId/trusted-contacts`
  - `PATCH /api/accounts/:accountId/trusted-contacts/:id`
  - `POST /api/accounts/:accountId/trusted-contacts/:id/remove`
- Audit event for every create, update, and removal

### Acceptance Criteria

- [ ] Creating a trusted contact requires at minimum `first_name`, `last_name`, and at least one of `phone` or `email`; missing all contact methods returns `400`
- [ ] `relationship_to_owner` accepts values from a defined enum: `spouse`, `parent`, `child`, `sibling`, `attorney`, `accountant`, `other`
- [ ] An account may have at most 3 active trusted contacts; attempting to add a 4th returns `400` with a descriptive message
- [ ] Removing a trusted contact requires a `removal_reason`; the record is soft-deleted (status set to `removed`, `date_removed` populated) and remains queryable for compliance
- [ ] Trusted contacts are returned in account detail responses as a nested array
- [ ] Trusted contact creation and removal emit audit events with actor, account, and timestamp
- [ ] `GET /api/accounts/:accountId/trusted-contacts` returns both active and removed contacts with status clearly indicated; supports filtering by `status`
- [ ] If the trusted contact is also a ClientPerson (referenced by `client_person_id`), updates to the ClientPerson name or contact info do not automatically propagate (trusted contact data is a point-in-time capture; updates require explicit action)
- [ ] The trusted contact cannot be the same person as the primary account owner (validated by `client_person_id` if provided, or by name+DOB heuristic if not)

### Dependencies

- Issue 5 (Account registration)
- Issue 2 (ClientPerson -- optional linkage)

---

## Issue 9: Authorized Signer Management

### Title

Implement authorized signer records for entity and fiduciary accounts

### Description

Entity accounts (corporate, LLC, partnership, trust) and certain fiduciary accounts require one or more authorized signers who are empowered to act on behalf of the account. Authorized signers are distinct from account owners, beneficiaries, and trusted contacts. The signer designation includes their authority level and the documentation supporting their authorization.

### Scope

- Postgres table `authorized_signers` with columns: `id` (UUID), `tenant_id`, `account_id`, `client_person_id`, `authority_level` (enum: `full`, `limited`, `trading_only`, `read_only`), `title` (e.g., "Trustee", "CFO", "Managing Partner"), `authorization_document_id` (nullable, FK to document metadata), `effective_date`, `expiration_date` (nullable), `status` (enum: `active`, `suspended`, `revoked`), `created_at`, `updated_at`, `created_by`
- REST endpoints:
  - `POST /api/accounts/:accountId/authorized-signers`
  - `GET /api/accounts/:accountId/authorized-signers`
  - `PATCH /api/accounts/:accountId/authorized-signers/:id`
  - `POST /api/accounts/:accountId/authorized-signers/:id/revoke`
- Audit events for creation, modification, and revocation

### Acceptance Criteria

- [ ] Authorized signers can only be added to accounts with entity or trust registration types; attempting to add a signer to an individual or IRA account returns `400`
- [ ] The `client_person_id` must reference an active ClientPerson within the same household as the account
- [ ] An account must have at least one authorized signer with `authority_level: "full"` to transition from `draft` to `pending_approval` (guard condition in account status lifecycle)
- [ ] Revoking the last `full` authority signer on an active account returns `400` with a message explaining the requirement
- [ ] `effective_date` defaults to today if not provided; `expiration_date` is optional and triggers a `signer_expiring_soon` event 30 days before expiration (can be stubbed as a future notification hook)
- [ ] Revoking a signer sets status to `revoked`, preserves the record for audit, and emits an audit event
- [ ] `GET /api/accounts/:accountId/authorized-signers` returns all signers including revoked ones; supports `status` filter
- [ ] Suspending a signer (temporary restriction) is a distinct action from revoking; suspended signers can be reactivated

### Dependencies

- Issue 5 (Account registration -- registration type check)
- Issue 2 (ClientPerson)
- Epic 5 (Document vault -- for authorization document references; nullable initially)

---

## Issue 10: External Bank Account Metadata

### Title

Implement external bank account metadata storage for funding and disbursement

### Description

External bank accounts are the source and destination for ACH transfers, wire transfers, and other money movement operations. The platform stores metadata about linked bank accounts but does not store full account credentials. Bank account verification status tracks whether micro-deposits, Plaid, or manual verification have confirmed ownership. This metadata is referenced by transfer cases and funding workflows.

### Scope

- Postgres table `external_bank_accounts` with columns: `id` (UUID), `tenant_id`, `household_id`, `client_person_id` (nullable), `client_entity_id` (nullable), `bank_name`, `account_type` (enum: `checking`, `savings`), `routing_number`, `account_number_last_four`, `account_number_encrypted`, `account_holder_name`, `verification_method` (enum: `micro_deposit`, `plaid`, `manual_review`, `none`), `verification_status` (enum: `unverified`, `pending`, `verified`, `failed`, `expired`), `verified_at` (nullable), `plaid_item_id` (nullable), `nickname`, `is_primary` (boolean), `status` (enum: `active`, `inactive`, `removed`), `created_at`, `updated_at`, `created_by`
- Unique constraint: one `is_primary: true` bank account per household
- REST endpoints:
  - `POST /api/households/:householdId/bank-accounts`
  - `GET /api/households/:householdId/bank-accounts`
  - `GET /api/bank-accounts/:id`
  - `PATCH /api/bank-accounts/:id`
  - `POST /api/bank-accounts/:id/verify`
  - `POST /api/bank-accounts/:id/remove`
- Kafka event: `bank_account.verified`, `bank_account.removed`

### Acceptance Criteria

- [ ] Creating a bank account stores `account_number_encrypted` via application-level encryption and persists `account_number_last_four` as a derived field
- [ ] The full account number is never returned in any API response; only `account_number_last_four`
- [ ] `routing_number` is validated as a 9-digit ABA routing number using checksum validation
- [ ] Creating a bank account with `is_primary: true` when another primary already exists for the household automatically demotes the existing primary to `is_primary: false`
- [ ] `POST /api/bank-accounts/:id/verify` with `verification_method: "micro_deposit"` transitions `verification_status` from `pending` to `verified` when correct deposit amounts are provided
- [ ] Verification with incorrect micro-deposit amounts increments a failure counter; after 3 failures, status transitions to `failed`
- [ ] A bank account with `verification_status: "unverified"` or `"failed"` cannot be used as a funding source for transfers (this constraint is enforced at the transfer service layer)
- [ ] Removing a bank account soft-deletes the record (`status: "removed"`) and retains it for audit; removed accounts are excluded from default list queries
- [ ] `GET /api/households/:householdId/bank-accounts` returns active and inactive accounts by default; supports `status` filter; `removed` accounts require explicit filter
- [ ] The `plaid_item_id` field is only populated for Plaid-verified accounts and is not exposed in client-facing API responses

### Dependencies

- Issue 1 (Household CRUD)
- Issue 2 (ClientPerson -- ownership reference)
- Issue 3 (ClientEntity -- ownership reference)
- Issue 11 (Sensitive data handling -- account number encryption)

---

## Issue 11: Sensitive Data Handling (SSN Encryption, PII Masking)

### Title

Implement application-level encryption for SSN/TIN and PII masking in API responses and logs

### Description

The platform stores highly sensitive personally identifiable information (PII) including Social Security Numbers, Tax Identification Numbers, and bank account numbers. This data must be encrypted at the application level before storage (not relying solely on Postgres TDE) and must never appear in API responses, application logs, error messages, or Kafka event payloads in cleartext. A dedicated encryption service module provides encrypt/decrypt operations with key rotation support.

### Scope

- Shared module `shared/encryption/` with:
  - `encrypt(plaintext: string): string` returning `{version}:{iv}:{ciphertext}`
  - `decrypt(encryptedValue: string): string` resolving the key version from the prefix
  - AES-256-GCM encryption with envelope pattern
  - Key material sourced from environment configuration (KMS integration stub for production)
  - Key version prefix on all ciphertext to support rotation
- Shared module `shared/masking/` with:
  - `maskSSN(ssn: string): string` returning `***-**-{last4}`
  - `maskAccountNumber(num: string): string` returning `****{last4}`
  - `maskEmail(email: string): string` returning `e***@domain.com`
  - `maskPhone(phone: string): string` returning `***-***-{last4}`
- Zod response schemas that exclude encrypted fields and include only masked/last-four derivatives
- Structured logging configuration that redacts fields matching patterns: `ssn`, `tin`, `account_number`, `routing_number`, `password`, `token`
- Kafka event payload sanitization middleware ensuring encrypted fields are never published

### Acceptance Criteria

- [ ] `encrypt("123-45-6789")` produces a string in format `v1:{base64_iv}:{base64_ciphertext}` that is different on every call (unique IV)
- [ ] `decrypt(encrypt("123-45-6789"))` returns `"123-45-6789"`
- [ ] Encrypting with key version `v1` and then rotating to `v2` still allows decryption of `v1`-prefixed ciphertext
- [ ] No API response schema in the `clients`, `accounts`, or `bank-accounts` modules includes a field named `ssn_encrypted`, `tin_encrypted`, or `account_number_encrypted`
- [ ] Application logs produced by the structured logger redact any value associated with keys matching the sensitive field pattern list
- [ ] A grep of the test suite HTTP response fixtures confirms zero occurrences of full SSN, TIN, or account number values in response bodies
- [ ] Kafka events for `client_person.created`, `client_entity.created`, and `bank_account.verified` contain only masked or last-four representations of sensitive data
- [ ] The encryption module exposes a `reencrypt(encryptedValue: string, newKeyVersion: string): string` function for batch key rotation jobs
- [ ] Unit tests confirm that encrypting an empty string or null input throws a descriptive error rather than producing corrupt ciphertext

### Dependencies

- None (this is a foundational shared module consumed by Issues 2, 3, 7, and 10)

---

## Issue 12: Household Dashboard Read Model

### Title

Implement denormalized household dashboard read model for fast advisor queries

### Description

Advisors need a consolidated view of each household showing summary data across clients, accounts, recent activity, and key metrics. Querying this from normalized Postgres tables with multiple joins on every dashboard load is expensive and fragile. A denormalized read model, materialized in Postgres initially (with the option to move to MongoDB later per the data architecture spec), provides a pre-computed household summary optimized for the advisor dashboard.

### Scope

- Postgres table `household_dashboard_views` with columns: `id` (UUID), `tenant_id`, `household_id` (unique per tenant), `household_name`, `primary_advisor_id`, `primary_advisor_name`, `client_count`, `account_count`, `active_account_count`, `total_aum_cents` (bigint, nullable -- populated by downstream portfolio integration), `account_summaries_json` (JSONB array of `{ account_id, display_name, registration_type, status, balance_cents }`), `client_summaries_json` (JSONB array of `{ client_id, type, display_name, status }`), `pending_tasks_count`, `last_activity_at`, `last_materialized_at`, `created_at`, `updated_at`
- Materialization strategy: event-driven updates via Kafka consumers
  - Listens to: `household.created`, `household.updated`, `client_person.created`, `client_entity.created`, `account.created`, `account.status_changed`, `advisor_relationship.created`, `advisor_relationship.ended`
  - On each relevant event, re-materializes the affected household's dashboard row
- REST endpoint:
  - `GET /api/households/:id/dashboard`
  - `GET /api/households/dashboards` (paginated list for advisor home screen)
- Response includes `last_materialized_at` for freshness transparency

### Acceptance Criteria

- [ ] `GET /api/households/:id/dashboard` returns the pre-computed dashboard view within 50ms for a household with up to 20 accounts and 10 clients
- [ ] Creating a new client person in a household triggers re-materialization; subsequent dashboard read reflects the updated `client_count` and `client_summaries_json`
- [ ] Creating a new account triggers re-materialization; `account_count` and `active_account_count` update correctly
- [ ] Closing an account triggers re-materialization; `active_account_count` decrements while `account_count` remains unchanged
- [ ] `GET /api/households/dashboards` supports filtering by `primary_advisor_id` and sorting by `household_name`, `total_aum_cents`, or `last_activity_at`
- [ ] The `last_materialized_at` timestamp is included in every response and reflects when the view was last recomputed
- [ ] If the dashboard view does not exist for a household (e.g., newly created, consumer lag), the endpoint falls back to a synchronous query that materializes and stores the view before returning
- [ ] Dashboard materialization is idempotent: processing the same event twice produces the same result
- [ ] The materialization consumer handles out-of-order events gracefully by always recomputing from source tables rather than applying incremental deltas
- [ ] `account_summaries_json` never includes sensitive data (no SSN, no full account numbers); only display-safe fields
- [ ] `total_aum_cents` is nullable and returns `null` until portfolio/balance integration is complete (Epic 11)

### Dependencies

- Issue 1 (Household CRUD)
- Issue 2 (ClientPerson)
- Issue 3 (ClientEntity)
- Issue 4 (Advisor relationships)
- Issue 5 (Account registration)
- Epic 4 (Kafka consumer infrastructure for event-driven materialization)
