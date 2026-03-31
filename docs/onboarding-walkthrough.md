# Client Onboarding Walkthrough

What actually happens, step by step, when a new client is onboarded onto the platform.

---

## The Scenario

Sarah Chen (advisor at Acme Wealth Advisors) is onboarding the Smith family. She needs to open three accounts for them in one session:

1. **Individual Brokerage** for John Smith
2. **Roth IRA** for Jane Smith
3. **Revocable Trust** account for the Smith Family Trust

---

## Phase 1: Case Creation

**Who acts:** Sarah (Advisor)

Sarah kicks off onboarding by creating an **onboarding case** — a tracked workflow envelope that wraps the entire process.

```
POST /api/onboarding-cases
```

She provides:
- The target household (existing "Smith Family" household, or a new one gets created inline)
- The three requested account registrations and their types
- An idempotency key (prevents duplicate cases if she double-clicks)

**What happens in the system:**
- A new `OnboardingCase` record is created in `draft` status
- The case is scoped to Acme Wealth's tenant — no other firm can see it
- Permission check confirms Sarah has `account.open` capability
- A `onboarding_case.created` event is published to Kafka
- An audit record is written (who, what, when)

```
Case Status: draft
```

---

## Phase 2: Legal Party Capture

**Who acts:** Sarah (Advisor)

Sarah adds the legal parties — the people and entities who will own the accounts.

```
POST /api/onboarding-cases/:id/parties
```

She adds three parties:
| Party | Type | Role |
|-------|------|------|
| John Smith | ClientPerson | Primary holder (individual brokerage) |
| Jane Smith | ClientPerson | Primary holder (Roth IRA) |
| Smith Family Trust | ClientEntity | Entity owner (trust account) |

For each person, the system captures:
- Name, DOB, SSN (encrypted at rest, only last 4 shown in API)
- Address, citizenship, phone, email
- Employment status, employer, occupation
- Regulatory flags (politically exposed? control person?)

For the trust, the system captures:
- Legal name, TIN (encrypted), entity type, formation date/state
- Governing document type (trust agreement)
- Trustee roles — John and Jane are linked as co-trustees

If John or Jane already exist in the client registry, Sarah can link them by ID instead of re-entering data. Otherwise, new `ClientPerson` / `ClientEntity` records are created in the registry automatically.

```
Case Status: draft (still)
```

---

## Phase 3: Client Data Collection (Optional)

**Who acts:** Sarah (Advisor) initiates, then John & Jane (Clients) complete

If the advisor doesn't have all the client details, she can hand part of the form to the clients themselves.

```
POST /api/onboarding-cases/:id/request-client-action
```

Sarah specifies which sections the clients need to fill in:
- `personal` — verify name, address, DOB
- `employment` — current employer, occupation
- `financial-profile` — income range, net worth, investment objectives, risk tolerance
- `regulatory` — affiliations, control person status, political exposure

The case transitions to `pending_client_action`. John and Jane get a link to a client-facing portal where they fill in their sections:

```
PUT /api/onboarding-cases/:id/client-data/personal
PUT /api/onboarding-cases/:id/client-data/employment
PUT /api/onboarding-cases/:id/client-data/financial-profile
PUT /api/onboarding-cases/:id/client-data/regulatory
```

Each section is independently validated and marked complete. These endpoints enforce **client-actor** authentication — Sarah's advisor token can't be used here (separation of concerns).

```
Case Status: pending_client_action
```

---

## Phase 4: Beneficiaries & Trusted Contacts

**Who acts:** Sarah and/or the clients

### Beneficiaries

Jane's Roth IRA requires beneficiary designations. Sarah adds:

```
POST /api/onboarding-cases/:id/beneficiaries
```

| Beneficiary | Type | Allocation |
|-------------|------|------------|
| John Smith | Primary | 100% |
| Sarah Smith (daughter) | Contingent | 50% |
| Michael Smith (son) | Contingent | 50% |

**Validation rules enforced:**
- Primary allocations must sum to exactly 100%
- Contingent allocations must independently sum to 100%
- IRA accounts cannot be submitted without beneficiaries

John's individual brokerage doesn't require beneficiaries (optional for non-retirement accounts).

### Trusted Contacts

FINRA Rule 4512 requires firms to make reasonable efforts to obtain a trusted contact. Sarah adds one:

```
POST /api/onboarding-cases/:id/trusted-contacts
```

| Field | Value |
|-------|-------|
| Name | Robert Smith (John's brother) |
| Relationship | Sibling |
| Phone | +1-555-0199 |
| Email | robert.smith@email.com |

Trusted contacts have no account authority — they're an emergency contact for situations like suspected exploitation or inability to reach the client.

```
Case Status: pending_client_action (or draft)
```

---

## Phase 5: Disclosures & Consent

**Who acts:** John & Jane (Clients), or Sarah on their behalf

Before the case can be submitted, the clients must acknowledge required legal disclosures. The system determines which disclosures are needed based on the account types in the case.

```
GET /api/onboarding-cases/:id/disclosures
```

Returns something like:

| Disclosure | Version | Status |
|------------|---------|--------|
| Advisory Agreement | v2.3 | pending |
| Privacy Policy | v1.8 | pending |
| Form CRS | v3.0 | pending |
| Account Agreement | v2.1 | pending |
| IRA Custodial Agreement | v1.4 | pending |

Each one gets accepted individually:

```
POST /api/onboarding-cases/:id/disclosures/:disclosureId/accept
```

Every acceptance creates an **immutable consent record** capturing:
- Who accepted (actor ID)
- When (timestamp)
- Which version
- IP address and user agent

These records can never be modified or deleted — they're the legal proof that the client saw and agreed to the terms.

**Submission gate:** The case cannot be submitted until every required disclosure is accepted.

```
Case Status: pending_client_action (or draft)
```

---

## Phase 6: Document Collection

**Who acts:** Sarah (Advisor) and/or clients

Depending on the account types and party types, certain documents are required.

```
POST /api/onboarding-cases/:id/documents
```

| Document | Classification | Required For |
|----------|---------------|--------------|
| John's driver's license | `government_id` | All accounts (identity verification) |
| Jane's passport | `government_id` | All accounts |
| Smith Family Trust Agreement | `trust_agreement` | Trust account |
| Utility bill | `proof_of_address` | All accounts |

Documents are uploaded through the document vault (separate system) and attached to the case. The system tracks which required document types have been satisfied based on a tenant-configurable matrix.

**Submission gate:** Missing required documents block submission.

```
Case Status: pending_client_action (or draft)
```

---

## Phase 7: Submission

**Who acts:** Sarah (Advisor)

Once all gates are green — parties captured, beneficiaries valid, disclosures accepted, documents uploaded — Sarah submits the case.

```
POST /api/onboarding-cases/:id/submit
```

**The submission gate checks everything:**
- All legal parties present with required fields
- All required disclosures accepted
- All required documents attached
- Beneficiary allocations valid (100% primary, 100% contingent) for retirement accounts
- All client data sections complete (if client action was requested)

If anything is missing, the submission is rejected with a detailed list of what's incomplete.

On success:

```
Case Status: draft/pending_client_action → submitted → pending_internal_review
```

The transition to `pending_internal_review` is automatic on submit. The case now lands in the operations team's review queue.

---

## Phase 8: Internal Review

**Who acts:** Operations / Compliance team

An operations user picks up the case from the review queue. They can optionally be assigned as the reviewer.

They check:
- Identity verified against documents
- Disclosures complete and version-current
- Trust agreement matches entity data
- No compliance red flags (PEP, control person disclosures)
- Data consistency across parties and accounts
- Tenant-specific checklist items

### Path A: Approved

Everything looks good. The reviewer approves:

```
POST /api/onboarding-cases/:id/approve
```

Requires `onboarding.approve` permission.

```
Case Status: pending_internal_review → approved
```

### Path B: Exception

The reviewer finds an issue — say, the trust agreement is missing a page.

```
POST /api/onboarding-cases/:id/flag-exception
```

They specify a category and reason:
- Category: `missing_document`
- Reason: "Trust agreement page 3 of 12 is missing — need complete copy"

```
Case Status: pending_internal_review → exception
```

The case appears in the operations exception queue. Sarah gets notified. She uploads the complete document, and operations resolves the exception:

```
POST /api/onboarding-cases/:id/resolve-exception
```

```
Case Status: exception → pending_internal_review
```

The reviewer re-reviews and approves.

### Path C: Rejected

If the case has an irrecoverable issue (e.g., identity verification fails, client is on a sanctions list):

```
POST /api/onboarding-cases/:id/reject
```

A reason is mandatory. Rejection is **terminal** — the case is closed permanently.

```
Case Status: pending_internal_review → rejected (terminal)
```

---

## Phase 9: Account Activation

**Who acts:** Operations team or automated system

After approval, activation is a **separate, explicit step** — not an automatic side effect. This is because downstream provisioning may need to happen first (opening the account at the custodian/clearing firm, assigning official account numbers).

```
POST /api/onboarding-cases/:id/activate
```

**What happens on activation:**

1. Each account linked to the case transitions to `active` in the account registry
2. Official account numbers are assigned (e.g., `ACC-20260329-001`, `ACC-20260329-002`, `ACC-20260329-003`)
3. External custodian account IDs are persisted (if provided)
4. Beneficiary and trusted contact records are written from the case to the account-level registry
5. Domain events fire:
   - `onboarding_case.activated` (one event for the case)
   - `account.activated` (one event per account — three in this scenario)
6. Audit trail records the activating actor, timestamp, and external references

```
Case Status: approved → activated (terminal)
```

The case is now **terminal and immutable**. Only notes can be added for post-activation annotations.

---

## The End Result

After activation, the Smith household has:

```
Smith Family Household
 │
 ├── John Smith (ClientPerson, active)
 │    └── ACC-20260329-001: Individual Brokerage (active)
 │
 ├── Jane Smith (ClientPerson, active)
 │    └── ACC-20260329-002: Roth IRA (active)
 │         ├── Beneficiary: John Smith (primary, 100%)
 │         ├── Beneficiary: Sarah Smith (contingent, 50%)
 │         └── Beneficiary: Michael Smith (contingent, 50%)
 │
 ├── Smith Family Trust (ClientEntity, active)
 │    ├── Trustees: John + Jane
 │    └── ACC-20260329-003: Trust Brokerage (active)
 │         └── Authorized Signer: John Smith (full authority)
 │
 └── Trusted Contact: Robert Smith (John's brother)
```

Sarah can now manage portfolios, place trades, and initiate transfers for all three accounts.

---

## Status Lifecycle (Summary)

```
draft
 ├──→ pending_client_action ──→ submitted
 └──→ submitted
          └──→ pending_internal_review
                  ├──→ approved ──→ activated ✓
                  ├──→ exception ──→ pending_internal_review (loop)
                  └──→ rejected ✗

exception ──→ rejected ✗  (can also reject directly from exception)
```

---

## What Gets Recorded

Every step of this process produces:

| Record Type | Durability | Purpose |
|-------------|-----------|---------|
| **Workflow history** | Immutable, append-only | Every status transition with actor + timestamp |
| **Audit events** | Immutable | Every state-changing operation for compliance |
| **Kafka domain events** | Published per action | Downstream systems react (notifications, analytics) |
| **Consent records** | Immutable, never deletable | Legal proof of disclosure acceptance |
| **Exception records** | Append-only | Full exception history with resolution trail |
| **Case notes** | Append-only | Advisor and reviewer annotations |

Nothing is silently lost. The onboarding case is a complete, auditable record of everything that happened from the moment the advisor clicked "Start Onboarding" to the moment the accounts went live.
