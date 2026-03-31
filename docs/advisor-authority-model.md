# Advisor Authority Model: How Advisors Act on Behalf of Clients

How and why an advisor can open accounts, place trades, and move money for a client — and why that's safe.

---

## The Core Concept

In an RIA (Registered Investment Advisor) platform, the **advisor** controls the client's accounts. The client does not log in and trade. This is fundamentally different from a retail brokerage like Robinhood or Schwab's self-directed platform.

| | RIA Platform (This System) | Retail Brokerage |
|-|---|---|
| **Who trades** | Advisor, on behalf of the client | Client themselves |
| **Who opens accounts** | Advisor, during onboarding workflow | Client, via self-service signup |
| **Who moves money** | Advisor creates transfer intents | Client initiates transfers |
| **Client's role** | Provide data, sign agreements, review reports | Full control of everything |
| **Client portal** | Read-only: view balances, statements, documents | Full trading and transfer access |

The client is paying the advisor specifically to manage their investments. They don't want to make every trade decision — that's the whole point of hiring an advisor.

---

## The Legal Foundation

### Investment Advisory Agreement (IAA)

Before an advisor can do anything, the client signs an **Investment Advisory Agreement**. This is captured during onboarding as an immutable consent record (see `docs/onboarding-walkthrough.md`, Phase 5).

The IAA grants the advisor **limited power of attorney** — legal authority to act on the client's behalf within defined boundaries.

### Two Authority Models

| Model | What the Advisor Can Do | Client Involvement | Typical Use |
|-------|------------------------|--------------------|-------------|
| **Discretionary** | Trade, rebalance, and manage the portfolio without asking each time | Reviews quarterly reports, can revoke anytime | Most RIAs (~90%) |
| **Non-Discretionary** | Propose trades, but must get explicit client approval before executing | Approves every trade before it goes to the OMS | Clients who want more control |

Most RIAs operate under **discretionary authority** — the client trusts the advisor's expertise and doesn't want to approve every rebalance.

### Fiduciary Duty

RIA advisors are held to a **fiduciary standard** — they are legally required to act in the client's best interest. This is stronger than the "suitability" standard that broker-dealers follow.

| Standard | Applies To | Meaning |
|----------|-----------|---------|
| **Fiduciary** | RIA advisors | Must act in client's best interest, disclose all conflicts |
| **Suitability** | Broker-dealers | Recommendation must be "suitable" but doesn't have to be the best option |

Violating fiduciary duty exposes the advisor and firm to SEC enforcement, lawsuits, and loss of registration.

---

## Why This Is Safe: The Layers of Protection

### Layer 1: Regulatory Oversight (External)

The advisor and the firm are regulated entities:

- **SEC or State Registration** — RIAs managing over $100M register with the SEC; smaller firms register with their state
- **Form ADV** — public disclosure document detailing the firm's business, fees, conflicts of interest, and disciplinary history
- **Annual Compliance Reviews** — required internal review of policies and procedures
- **SEC Examinations** — the SEC can (and does) audit RIA firms
- **FINRA Rules** — broker-dealer regulations apply to trading and custody (Rule 4512 for trusted contacts, etc.)

### Layer 2: Custodial Separation (Structural)

This is the most important safety mechanism. **The advisor never holds the client's money.**

```
┌──────────────────────────────────────────────┐
│            Client's Assets                    │
│                                              │
│   Held by: Custodian (Trading Platform)      │
│   NOT held by: The Advisor or RIA Firm       │
│                                              │
│   Client receives statements directly        │
│   Client can view custodian portal anytime   │
│   Assets belong to client, not the advisor   │
└──────────────────────────────────────────────┘
         │                          │
         │ Statements go            │ Instructions come
         │ directly to client       │ from advisor
         ▼                          │
┌─────────────────┐    ┌───────────▼──────────┐
│     Client      │    │   Advisor (via RIA   │
│                 │    │      Platform)        │
│  Can see        │    │                      │
│  everything     │    │  Can trade and       │
│  independently  │    │  move money, but     │
│                 │    │  only to/from the    │
│                 │    │  client's verified   │
│                 │    │  bank accounts       │
└─────────────────┘    └──────────────────────┘
```

What custodial separation prevents:

| Threat | How It's Prevented |
|--------|--------------------|
| Advisor steals client funds | Money can only move to/from the client's own verified bank account — never to the advisor's |
| Advisor hides losses | Custodian sends statements directly to the client — advisor can't alter them |
| Advisor fabricates trades | Custodian independently records all executions and positions |
| Advisor disappears | Assets are held by the custodian, not the advisor — client still owns everything |
| Firm goes bankrupt | Assets are custodied separately from the firm's own accounts — client assets are not firm assets |

### Layer 3: Platform Controls (This System)

Everything the advisor does is tracked and constrained by this platform:

**Audit Trail**
- Every action produces an immutable audit event (who, what, when, from where)
- Workflow history records every status transition with the acting user
- Consent records are append-only and cannot be deleted
- Exception records capture every compliance flag and resolution

**Access Scoping**
- Advisors can only see households where they have an active relationship
- One advisor cannot see another advisor's clients
- Tenant isolation prevents cross-firm access entirely
- MFA is mandatory for all advisors

**Approval Gates**
- Wire transfers above configurable thresholds require operations approval before submission
- Onboarding cases require internal review before accounts are activated
- Exception states force human review before workflows can continue

**Immutability**
- Consent records (disclosure acceptance) cannot be modified or deleted
- Status transition history is append-only
- Document vault records are preserved even if detached from a case
- Rejected and activated cases are terminal and immutable

### Layer 4: Client Protections (Always Available)

The client is never locked in:

| Protection | How It Works |
|-----------|--------------|
| **Revocation** | Client can revoke the advisory agreement at any time — advisor loses authority immediately |
| **Transfer out** | Client can ACAT their accounts to another firm at any time |
| **Direct custodian access** | Client can view their accounts directly at the custodian, independent of the advisor |
| **Complaints** | Client can file complaints with the SEC, state regulators, or FINRA |
| **Independent statements** | Custodian mails/emails statements directly to the client — advisor cannot suppress them |

---

## What Each Party Can Do in This Platform

### Advisor (User with `advisor` role)

| Action | Allowed | Requires |
|--------|---------|----------|
| Create onboarding case | Yes | `account.open` permission |
| Add clients to household | Yes | `client.write` permission |
| Open accounts | Yes | `account.open` permission, goes through review workflow |
| Place trades / rebalance | Yes | `order.submit` permission, goes through OMS |
| Initiate transfers (ACH, ACAT, wire) | Yes | `transfer.submit` permission |
| Initiate wire above threshold | Yes | Requires additional operations approval |
| View own clients' data | Yes | Active advisor relationship to household |
| View other advisors' clients | No | Relationship check blocks access |
| Approve onboarding cases | No | Requires `onboarding.approve` (operations role) |
| Manage users / firm settings | No | Requires `firm_admin` role |

### Client

| Action | Allowed | Context |
|--------|---------|---------|
| Provide personal data during onboarding | Yes | Client-facing data capture endpoints (Phase 3 of onboarding) |
| Accept disclosures | Yes | Creates immutable consent record |
| View own accounts, balances, reports | Yes | Read-only client portal |
| Place trades | No | Not available in client portal |
| Initiate transfers | No | Must go through advisor |
| Revoke advisory agreement | Yes | Outside the platform (legal process) |
| Transfer accounts to another firm | Yes | Initiates ACAT via new firm's advisor |

### Operations / Compliance

| Action | Allowed | Context |
|--------|---------|---------|
| Review onboarding cases | Yes | `onboarding.approve` permission |
| Approve / reject cases | Yes | `onboarding.approve` permission |
| Flag exceptions | Yes | `onboarding.approve` or `operations` role |
| Approve wire transfers above threshold | Yes | Approval workflow |
| View all households in the firm | Yes | Not scoped to advisor relationships |
| Activate accounts | Yes | `onboarding.activate` permission |

---

## The Real-World Analogy

Think of it like hiring a property manager for a rental property you own:

| Analogy | RIA Equivalent |
|---------|---------------|
| You own the property | Client owns the assets (held at custodian) |
| You sign a management agreement | Client signs advisory agreement (IAA) |
| Property manager collects rent, handles repairs, manages tenants | Advisor trades, rebalances, manages portfolio |
| Property manager can't sell your house or pocket the rent | Advisor can't move money to their own account |
| You get monthly statements from the bank | Client gets statements directly from custodian |
| You can fire the property manager anytime | Client can revoke the IAA anytime |
| The property manager is licensed and regulated | The advisor is registered with SEC/state and has fiduciary duty |

---

## How This Shapes the Platform Architecture

The advisor-authority model is why this platform exists as a separate layer on top of the trading platform:

```
The Trading Platform (Custodian)
  - Knows about: accounts, positions, orders, balances
  - Doesn't know about: households, advisor relationships, onboarding workflows,
    fiduciary agreements, compliance review, or who authorized what

This Platform (Advisor Control Plane)
  - Knows about: who the advisor is, who their clients are, what the advisor
    is authorized to do, the workflow to do it safely, and the audit trail
  - Delegates to: the trading platform for actual financial execution
```

The trading platform provides the plumbing. This platform provides the governance — making sure the right person is doing the right thing, with the right authorization, through the right workflow, with a complete audit trail.

---

## Summary

| Question | Answer |
|----------|--------|
| Can an advisor open accounts for a client? | Yes — that's the normal workflow |
| Does the client know? | Yes — they signed the advisory agreement and accepted disclosures during onboarding |
| Can the advisor steal money? | No — money only moves to/from the client's verified bank accounts, custodian holds assets independently |
| Can the client see what's happening? | Yes — custodian sends statements directly, client portal provides read-only access |
| What if the advisor does something wrong? | Immutable audit trail, SEC/state oversight, fiduciary liability, client can revoke and transfer out |
| Is this how the real industry works? | Yes — this is standard for every RIA managing discretionary accounts |
