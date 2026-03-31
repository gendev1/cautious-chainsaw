# Service Boundaries: What Lives Here vs. What Lives in the Trading Platform

---

## The Key Insight

This platform is **not** a trading platform. It sits **on top of** one.

The trading platform company already has microservices that handle the core financial infrastructure — custody, clearing, order management, execution, money movement. Those systems are authoritative for their domains. They already know how to open a custodial account, execute a trade, and settle a transfer.

This platform is the **advisor-facing control plane** — it owns the workflow, the client experience, and the orchestration. It tells the trading platform *what* to do and *when*, but the trading platform does the actual financial plumbing.

```
┌─────────────────────────────────────────────────────────┐
│              THIS PLATFORM (Wealth Advisor)              │
│                                                         │
│  "The advisor's operating system"                       │
│                                                         │
│  Owns: who the clients are, what they want to do,       │
│        the workflow to get it done, and the audit trail  │
└──────────────────────────┬──────────────────────────────┘
                           │  API calls / Kafka events
                           │
┌──────────────────────────▼──────────────────────────────┐
│          TRADING PLATFORM (External Services)            │
│                                                         │
│  "The financial plumbing"                               │
│                                                         │
│  Owns: custodial accounts, order execution, settlement, │
│        money movement rails, security/market data       │
└─────────────────────────────────────────────────────────┘
```

---

## How Onboarding Differs from "Opening a Trading Account"

### What the trading platform already does

When you open an account directly at the trading platform (custodian/clearing level), the process is roughly:

1. Submit account application data (name, SSN, account type)
2. Platform runs KYC/AML checks
3. Account is provisioned — gets a custodial account number
4. Account is ready to trade

That's a **single-system, data-in → account-out** pipeline. The trading platform owns the entire process.

### What THIS platform adds on top

This platform doesn't replace that. It wraps it in an **advisor workflow layer**:

| Step | This Platform (Advisor Layer) | Trading Platform (Custodian) |
|------|-------------------------------|------------------------------|
| 1. Advisor starts onboarding | Creates an onboarding case, links household, picks account types | Nothing yet — doesn't know about this |
| 2. Client data collection | Captures personal info, employment, financial profile, regulatory disclosures via advisor + client portal | Nothing yet |
| 3. Document collection | Uploads IDs, trust agreements, proof of address into document vault | Nothing yet |
| 4. Disclosures & consent | Presents advisory agreement, privacy policy, Form CRS — records immutable consent | Nothing yet |
| 5. Beneficiaries & contacts | Captures beneficiary designations, trusted contacts (FINRA 4512) | Nothing yet |
| 6. Internal review | Operations/compliance team reviews the case, flags exceptions, approves or rejects | Nothing yet |
| 7. Approved → Activation | **NOW** the platform calls downstream to provision the account | Receives the account open request, runs KYC, provisions custodial account, returns account number |
| 8. Post-activation | Stores custodial account ID, marks accounts active, publishes events | Account is live, ready for trades and transfers |

**The trading platform only gets involved at step 7.** Everything before that is workflow orchestration that the trading platform doesn't care about — it's advisor-specific, firm-specific, compliance-specific overhead that varies by RIA.

### Why the separation exists

A single trading platform serves many types of customers — retail brokers, RIAs, institutional desks. Each has different:
- Compliance requirements (an RIA needs advisory agreements; a retail broker doesn't)
- Workflow needs (an RIA needs internal review and advisor approval; a retail app doesn't)
- Data models (an RIA groups clients into households with advisor relationships; a retail app has individual users)
- Document requirements (trusts need trust agreements; individual retail accounts don't)

The trading platform can't bake all of that in. So this platform handles the advisor-specific workflow and only talks to the trading platform when it's time to actually provision something.

---

## What Lives in THIS Microservice

Everything related to **who the clients are, how they're organized, and the workflow to get things done**:

### Client & Account Registry
- Households (family groupings)
- Client persons and entities (the people and trusts)
- Advisor-to-household relationships
- Account registrations (legal structure, titling, ownership)
- Beneficiaries, trusted contacts, authorized signers

**Why here:** The trading platform doesn't know about households, advisor relationships, or family groupings. It knows about custodial accounts. This platform adds the relational layer on top.

### Onboarding Workflow
- Case lifecycle (draft → submitted → reviewed → approved → activated)
- Client data collection (sections, completion tracking)
- Disclosure management (versioned disclosures, immutable consent records)
- Document collection and completeness tracking
- Exception handling (flagging, resolution, re-review)
- Internal review and approval

**Why here:** The trading platform's account opening is a single API call. This platform turns it into a multi-step, multi-actor workflow with audit trails.

### Transfer Workflow
- Transfer intent creation and validation
- Advisor-initiated ACAT requests
- In-flight monitoring and status tracking
- Exception handling

**Why here:** The trading platform's money movement service handles the actual ACAT/ACH/wire rail. This platform handles the advisor workflow around it — who requested it, was it approved, what's the status, alert me if it's stuck.

### Trading Workflow (Intent Layer)
- Trade proposals and model-driven rebalancing
- Advisor review and approval of proposed trades
- Order intent submission

**Why here:** The OMS handles order acceptance, routing, execution, and fills. This platform handles the advisor-facing part — "here's what the model suggests, do you approve?" Once approved, it submits the order intent to the OMS.

### Billing & Reporting Orchestration
- Fee schedule definitions
- Billing calendar and run orchestration
- Report generation orchestration

**Why here:** Fee structures and billing schedules are firm-specific. The trading platform may provide raw position/transaction data, but this platform defines how fees are calculated and when reports are generated.

### Tenant, Identity & Permissions
- Firm (tenant) management
- Users, roles, permissions
- MFA, sessions, invitations
- Advisor-to-household access scoping

**Why here:** The trading platform has its own auth, but this platform's multi-tenant, role-based access model is specific to the RIA use case.

### Documents & Records
- Document vault (uploads, metadata, versioning)
- Retention policies
- Immutable signed artifact references

**Why here:** Trust agreements, advisory contracts, and compliance documents are advisor-workflow artifacts. The trading platform doesn't manage them.

---

## What Gets Offloaded to the Trading Platform

Everything related to **actually executing financial operations**:

| Domain | External Service | What It Owns |
|--------|-----------------|--------------|
| **Custody & Clearing** | Custodian / Clearing Firm | Custodial account provisioning, account numbers, holding records, settlement |
| **Order Management** | OMS / EMS | Order acceptance, validation, routing, execution, fills, cancellations |
| **Money Movement** | Transfer Rails Service | ACAT submission, ACH initiation, wire transfers, verification, returns/reversals |
| **Market Data** | Security Master / Reference Data | Security metadata, pricing, classifications, benchmarks, instrument eligibility |
| **Positions & Balances** | Custody / Clearing | Authoritative position and cash balance data |
| **Settlement** | Clearing Firm | Trade settlement, reconciliation |

---

## The Integration Pattern

This platform talks to the trading platform in two ways:

### Synchronous (API / gRPC) — "Do this now and tell me if it worked"
- Submit an order intent to the OMS
- Request account provisioning at the custodian
- Look up a security in the security master
- Initiate an ACAT transfer

### Asynchronous (Kafka events) — "Tell me when something changes"
- Order fills and cancellations come back as events
- ACAT lifecycle updates (submitted, in-progress, completed, failed)
- ACH return/reversal events
- End-of-day position and price snapshots

```
This Platform                          Trading Platform
     │                                       │
     │──── POST /accounts (provision) ──────►│
     │◄─── 201 { custodian_account_id } ─────│
     │                                       │
     │──── POST /orders (submit intent) ────►│
     │◄─── 202 { order_id } ─────────────────│
     │                                       │
     │◄─── Kafka: order.filled ──────────────│
     │◄─── Kafka: order.cancelled ───────────│
     │◄─── Kafka: transfer.completed ────────│
     │◄─── Kafka: positions.eod_snapshot ────│
```

---

## Example: The Full Picture for Onboarding

```
Advisor clicks "Start Onboarding"
         │
         ▼
┌─── THIS PLATFORM ────────────────────────────────────────┐
│                                                          │
│  1. Create onboarding case (draft)                       │
│  2. Capture client data (persons, entities, addresses)   │
│  3. Collect documents (IDs, trust agreements)            │
│  4. Present & record disclosures (advisory agreement)    │
│  5. Capture beneficiaries & trusted contacts             │
│  6. Submit for internal review                           │
│  7. Operations reviews, approves                         │
│  8. ─── ACTIVATION HANDOFF ──────────────────────────────┼──┐
│  9. Store custodial account ID, mark accounts active     │  │
│  10. Publish events, update registry                     │  │
│                                                          │  │
└──────────────────────────────────────────────────────────┘  │
                                                              │
         ┌────────────────────────────────────────────────────┘
         ▼
┌─── TRADING PLATFORM ─────────────────────────────────┐
│                                                      │
│  8a. Receive account provisioning request            │
│  8b. Run KYC/AML (if custodian handles this)         │
│  8c. Provision custodial account                     │
│  8d. Return custodial account number                 │
│                                                      │
└──────────────────────────────────────────────────────┘
```

Steps 1–7 and 9–10 are **this platform**. Step 8 is the only moment the trading platform is involved.

---

## The "Already Has Accounts Elsewhere" Scenario

When a client already has accounts at another custodian and is transferring to this firm:

| Step | Who Handles It |
|------|---------------|
| Advisor creates a transfer case | **This platform** — workflow, validation, intent capture |
| Client provides prior account details | **This platform** — data collection within the case |
| New accounts are opened at this custodian | **This platform** orchestrates → **Trading platform** provisions |
| ACAT transfer is initiated | **This platform** submits intent → **Trading platform's** money movement service submits to NSCC/ACATS |
| Transfer status updates flow back | **Trading platform** publishes Kafka events → **This platform** consumes and updates case status |
| Assets arrive in new accounts | **Trading platform** settles → **This platform** shows the advisor "transfer complete" |

The advisor never talks to the trading platform directly. This platform is the single pane of glass.

---

## Summary: The Mental Model

Think of it as two layers:

| | This Platform | Trading Platform |
|-|---------------|-----------------|
| **Metaphor** | The front desk and case management system at a law firm | The courthouse and banking system |
| **Cares about** | Who the client is, what they need, who's handling it, what's the status, is it compliant | Can this trade execute, did the money move, what's the position |
| **Data model** | Households, advisor relationships, cases, disclosures, documents | Custodial accounts, orders, fills, positions, balances |
| **Workflow** | Multi-step, multi-actor, approval-gated, audited | Single-step execution, event-driven status |
| **Users** | Advisors, operations staff, compliance, clients | API consumers (this platform is one of them) |
