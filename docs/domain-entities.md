# Domain Entities: Tenants, Advisors, Households, Clients & Accounts

This document explains the core domain entities in the wealth advisor platform, how they differ from each other, and how they relate.

---

## At a Glance

```
Tenant (Firm)
 └── User / Advisor
      └── Household
           ├── Client (Person)
           ├── Client (Entity — trust, LLC, corp)
           └── Account
                ├── Registration (legal title/structure)
                ├── Beneficiaries
                └── Trusted Contacts
```

Every entity below the Tenant is scoped to that tenant. Cross-tenant access is impossible by design.

---

## 1. Tenant (Firm)

**What it is:** The RIA (Registered Investment Advisor) firm that subscribes to the platform. This is the root of all data isolation — think of it as the "company" in a multi-tenant SaaS.

**Real-world analogy:** The wealth management firm itself — "Acme Wealth Advisors."

| Property | Detail |
|----------|--------|
| Identity | `id` (UUID), `name`, `slug` (used for subdomain routing: `{slug}.wealthadvisor.com`) |
| Lifecycle | `provisioning` → `active` ↔ `suspended` → `deactivated` |
| Branding | JSONB blob for logo, colors, custom UI |
| Children | All users, households, accounts, cases, documents |

**Key rule:** Every database query and API request is filtered by `firm_id`. A user in Firm A can never see data belonging to Firm B.

---

## 2. Advisor (User with an Advisor Role)

**What it is:** A human user within the firm who manages client relationships. "Advisor" is **not** a separate entity — it is a **role assignment** on a `User` record. A user can hold multiple roles simultaneously (e.g., `advisor` + `trader`).

**Real-world analogy:** The financial advisor at the firm who manages a "book of business."

| Property | Detail |
|----------|--------|
| Identity | `id`, `firm_id`, `email` (unique per firm), `display_name` |
| Auth | Password hash, MFA factors, sessions, refresh tokens |
| Lifecycle | `invited` → `active` → `disabled` |
| Roles | `advisor`, `firm_admin`, `operations`, `trader`, `billing_admin`, `viewer`, `support_impersonator` |

**Advisor ≠ Client:** Advisors are internal staff who operate the platform. Clients are the end-customers whose money is managed.

### How Advisors Connect to Households

Through an `advisor_relationships` table:

| Relationship Type | Meaning |
|-------------------|---------|
| `primary_advisor` | Owns the relationship — exactly one per household |
| `secondary_advisor` | Collaborator with access |
| `service_associate` | Operational support |
| `operations_support` | Back-office support |

An advisor can only see households where they have an active relationship. Operations/compliance users can see all households in their firm.

---

## 3. Household

**What it is:** The top-level grouping for related clients and their accounts. A household can represent a family, a married couple, or even a single individual. All clients within a household share common context (address, documents, advisor team).

**Real-world analogy:** The "family file" at a wealth management firm — the Smiths and all their accounts under one roof.

| Property | Detail |
|----------|--------|
| Identity | `id`, `tenant_id`, `name` (e.g., "Smith Family") |
| Lifecycle | `active` ↔ `inactive` → `closed` |
| Advisor | `primary_advisor_id` (required), plus a `service_team_json` array |
| Children | Client persons, client entities, accounts |

**Key rules:**
- A household must have exactly one primary advisor.
- A closed household cannot have new clients or accounts added.
- An account always belongs to exactly one household.

---

## 4. Client (Person & Entity)

Clients are the actual customers whose money is managed. There are two types:

### ClientPerson — A Natural Person

**What it is:** An individual human — the person who owns accounts and signs documents.

**Real-world analogy:** John Smith, the client sitting across the desk from the advisor.

| Property | Detail |
|----------|--------|
| Identity | `first_name`, `last_name`, `date_of_birth`, `ssn` (encrypted) |
| Contact | `email`, `phone`, mailing/residential addresses |
| Regulatory | `citizenship_country`, `employment_status`, `is_politically_exposed`, `is_control_person` |
| Lifecycle | `draft` → `active` ↔ `inactive` |
| Parent | Household (`household_id` — immutable once set) |

### ClientEntity — A Legal Entity

**What it is:** A trust, corporation, LLC, partnership, or other legal structure that can own accounts.

**Real-world analogy:** The "Smith Family Trust (2020)" or "Smith Holdings LLC."

| Property | Detail |
|----------|--------|
| Identity | `legal_name`, `entity_type`, `tin` (encrypted), `formation_date`, `formation_state` |
| Types | `revocable_trust`, `irrevocable_trust`, `c_corporation`, `s_corporation`, `llc`, `general_partnership`, `limited_partnership`, `non_profit`, `estate` |
| Roles | Linked to ClientPerson records as `trustee`, `grantor`, `officer`, `director`, `partner`, `manager`, `authorized_representative` |
| Parent | Household (`household_id`) |

### Client ≠ Advisor, Client ≠ User

| | Client | Advisor (User) |
|-|--------|----------------|
| **Who** | End-customer whose money is managed | Internal staff at the firm |
| **Logs in?** | No (in the current platform model) | Yes — email + password + MFA |
| **Holds accounts?** | Yes — owns brokerage, IRA, trust accounts | No |
| **Scoped to** | Household | Firm |
| **PII** | SSN/TIN encrypted, heavy regulatory data | Email + display name |

---

## 5. Account

**What it is:** The actual brokerage/investment account that holds securities, cash, and positions. Every account has two facets:

1. **Account** — the operational container (account number, status, holdings)
2. **AccountRegistration** — the legal title/structure (who owns it and how)

**Real-world analogy:** The brokerage account you see on a statement — "Account #12345, John Smith Individual Brokerage."

| Property | Detail |
|----------|--------|
| Identity | `account_number` (unique per tenant), `display_name` |
| Lifecycle | `draft` → `pending_approval` → `active` ↔ `restricted` → `closed` |
| Parent | Household (`household_id`) |
| Registration | One-to-one link to an `AccountRegistration` |
| Children | Beneficiaries, trusted contacts, authorized signers, status transitions |

### Registration Types

The registration determines the legal structure and drives validation rules:

| Category | Types | Owner Requirements |
|----------|-------|--------------------|
| **Individual** | `individual` | One person |
| **Joint** | `joint_tenants_wros`, `joint_tenants_in_common`, `joint_community_property` | Two persons (same household) |
| **Retirement** | `traditional_ira`, `roth_ira`, `sep_ira`, `rollover_ira`, `inherited_ira`, etc. | One person; beneficiaries **required** |
| **Minor** | `ugma`, `utma` | Custodian person + minor person |
| **Entity** | `revocable_trust`, `irrevocable_trust`, `corporate`, `llc`, `partnership` | Entity; authorized signers **required** |
| **Special** | `529_plan`, `coverdell_esa`, `hsa`, `donor_advised_fund` | Varies |

### Tax Status (derived from registration)

| Tax Status | Registration Types |
|------------|-------------------|
| `taxable` | Individual, joint, entity |
| `tax_deferred` | Traditional IRA, SEP IRA, 401(k) |
| `tax_exempt` | Roth IRA, Roth 401(k), HSA, 529 |

---

## How They All Fit Together

### The Hierarchy

```
Firm ("Acme Wealth Advisors")                    ← Tenant
 │
 ├── Sarah Chen (User, role: advisor)             ← Advisor
 │    └── primary_advisor for → Smith Household
 │
 └── Smith Household                              ← Household
      │
      ├── John Smith (ClientPerson)               ← Client
      │    ├── Individual Brokerage (Account)
      │    └── Traditional IRA (Account)
      │         └── Beneficiary: Jane Smith, 100%
      │
      ├── Jane Smith (ClientPerson)               ← Client
      │    └── Roth IRA (Account)
      │
      ├── Smith Family Trust (ClientEntity)       ← Client (Entity)
      │    ├── Trustees: John + Jane
      │    └── Trust Brokerage (Account)
      │         └── Authorized Signer: John (full)
      │
      └── Joint Brokerage (Account)
           └── Owners: John + Jane (JTWROS)
```

### Access Flow

```
API Request arrives
  → Middleware resolves Tenant from subdomain/token
  → Auth checks User identity + MFA
  → Permission check: does this User's role allow the action?
  → Relationship check: does this Advisor have a relationship to the target Household?
  → Tenant scoping: query filtered by firm_id at the repository layer
  → Response: only data the advisor is authorized to see
```

---

## Quick Reference: Entity Comparison

| | Tenant | Advisor | Household | Client | Account |
|-|--------|---------|-----------|--------|---------|
| **Is a...** | Organization | Internal user | Family grouping | End-customer | Investment container |
| **Parent** | None (root) | Tenant | Tenant | Household | Household |
| **Identified by** | Slug / UUID | Email + firm | Name + advisor | SSN/TIN + name | Account number |
| **Can own accounts?** | No | No | Contains them | Yes (person or entity) | N/A |
| **Authenticates?** | N/A | Yes (email + MFA) | N/A | No | N/A |
| **Has PII?** | No | Minimal | No | Heavy (SSN, DOB, address) | Derived from client |
| **Cardinality** | 1 per firm | Many per firm | Many per firm | Many per household | Many per household |
