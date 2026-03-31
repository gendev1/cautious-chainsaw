# Client Departure: What Happens When a Client Fires Their Advisor

Two scenarios play out depending on where the client wants to go next.

---

## Scenario A: Client Leaves the Advisor but Stays at the Same Custodian

The client is happy with the custodian (trading platform) but wants a different advisor — either another advisor within the same firm, or an advisor at a different RIA that also uses this custodian.

### A1: Reassignment Within the Same Firm

The client says: *"I don't want to work with Sarah anymore. Can I work with David instead?"*

This is the simplest case. No money moves. No accounts close. It's an internal relationship change.

```
What Happens in the Platform
─────────────────────────────
1. Firm admin or operations updates the advisor relationship
   - Sarah's primary_advisor relationship → status: 'ended', end_date: today
   - David gets a new primary_advisor relationship → status: 'active'

2. Sarah immediately loses visibility to the household
   - Her next API call for the Smith household returns 404
   - Access scoping is enforced by advisor_relationships

3. David immediately gains visibility
   - The household, all clients, and all accounts appear in his dashboard

4. Audit trail records the change
   - Who made the change, when, why

5. Nothing happens at the custodian
   - Account numbers don't change
   - Positions don't move
   - The custodian doesn't even know this happened
```

**What changes:**

| Entity | What Happens |
|--------|-------------|
| Household | `primary_advisor_id` updated to David |
| Advisor relationship (Sarah) | `status: ended`, `end_date` set |
| Advisor relationship (David) | New record created, `status: active` |
| Accounts | Nothing — same account numbers, same custodian |
| Client records | Nothing — same data, same household |
| Positions & balances | Nothing — held at custodian, unaffected |

**What the client experiences:** Nothing changes except who calls them. Same login, same accounts, same balances.

### A2: Move to a Different RIA Firm (Same Custodian)

The client says: *"I'm leaving Acme Wealth entirely. I'm going to work with Beta Advisors, and they also use this custodian."*

This is more complex because the client is crossing tenant boundaries in this platform, but the assets don't actually move at the custodian level.

```
What Happens
────────────
At the NEW firm (Beta Advisors — different tenant in this platform):
  1. Beta's advisor creates an onboarding case for the client
  2. Client provides data (or Beta imports it)
  3. Normal onboarding workflow: disclosures, documents, review, approval
  4. Accounts are "re-registered" — the custodian reassigns them to Beta's
     master account or creates new custodial accounts linked to Beta

At the OLD firm (Acme Wealth — Sarah's tenant):
  5. Acme receives notification that the client is transferring
  6. Operations marks accounts as restricted (reason: client_departure)
  7. Once custodian confirms the transfer: accounts → closed
  8. Household → inactive → closed (once all accounts are closed)
  9. Client records remain for audit (never hard-deleted)
  10. Sarah loses all visibility
```

This is essentially an **outbound ACAT at the custodian level**, even though the assets might not physically move between institutions — the custodian is just re-papering the accounts under a different advisor/firm.

---

## Scenario B: Client Leaves and Transfers to a Completely Different Custodian

The client says: *"I'm done with all of you. I'm moving my money to Fidelity."*

This is the full departure — assets leave the custodian entirely via ACAT.

### The Timeline

```
Day 0: Client Notifies Advisor (or just goes to Fidelity directly)
─────────────────────────────────────────────────────────────────────

The client doesn't actually need to tell the current advisor.
They walk into Fidelity (or go online) and say "I want to transfer
my accounts from [current custodian]." Fidelity initiates the ACAT.

The first sign the current advisor sees may be an inbound ACAT
notification from the custodian.
```

```
Day 0-1: ACAT Initiated (at the receiving firm — Fidelity)
───────────────────────────────────────────────────────────

Fidelity submits a Transfer Initiation Form (TIF) to NSCC/ACATS
  → NSCC routes it to the current custodian (trading platform)
  → The custodian notifies this platform via Kafka event
```

```
Day 1-2: This Platform Receives the ACAT Notification
──────────────────────────────────────────────────────

What happens in this platform:

1. Kafka consumer ingests the inbound ACAT event
   - Event contains: account number, contra firm (Fidelity), transfer type (full/partial)

2. Platform matches the event to an account in the registry
   - Looks up by custodian_account_id

3. Account status transitions: active → restricted
   - restricted_reason: 'acat_outbound'
   - This prevents new trades from being placed

4. An operational task is auto-generated
   - Type: 'acat_outbound_review'
   - Assigned to operations queue
   - "Client [Smith] account [ACC-001] has an outbound ACAT to Fidelity"

5. Advisor (Sarah) sees a notification
   - "Your client John Smith's account has a pending outbound transfer"

6. Audit events are recorded
```

```
Day 2-3: Review Window (Contra Firm Review Period)
──────────────────────────────────────────────────

The current custodian has a window to review the ACAT:

- Verify the account details match
- Check for any holds or restrictions (legal holds, margin balances, etc.)
- Flag any assets that can't transfer (proprietary funds, restricted stock)

If there are issues:
  → Custodian rejects the ACAT with a reason code
  → Platform receives rejection event via Kafka
  → Account restriction is lifted: restricted → active
  → Operational task updated with rejection reason

If everything is clean:
  → Custodian accepts the ACAT
  → Platform receives acceptance event: "ACAT in transit"
```

```
Day 3-8: Assets In Transit
──────────────────────────

Assets are moving through NSCC/ACATS:

1. Positions are liquidated or transferred in-kind
   - Most positions transfer as-is (in-kind)
   - Some positions may need to be liquidated first
     (proprietary funds, fractional shares)

2. This platform tracks status via Kafka events:
   - transfer.in_transit → assets are moving
   - Status visible in the advisor and operations dashboards

3. Account remains restricted
   - No new trades, no new contributions
```

```
Day 8-10: Transfer Complete
───────────────────────────

Custodian confirms all assets have been delivered to Fidelity.

What happens in this platform:

1. Kafka event: transfer.completed
   - All positions and cash have been delivered

2. Account balance drops to $0

3. Operations reviews and closes the account:
   POST /api/accounts/:id/close

   Guard conditions checked:
   - No open positions remaining ✓ (all transferred)
   - No pending transfers ✓ (ACAT completed)
   - No pending trades ✓ (account was restricted)

   Account status: restricted → closed
   - closed_date: today
   - Kafka event: account.closed

4. If ALL accounts in the household are now closed:
   POST /api/households/:id/close

   Household status: active → inactive → closed

5. Advisor relationship ends
   - Sarah's relationship: status → 'ended'
   - Sarah loses visibility to the household

6. Final billing
   - Pro-rated fee calculated for the partial period
   - Billing run includes the closed account through its closure date
   - After final fee is collected, billing relationship ends

7. All records preserved
   - Client records: remain (never deleted — regulatory retention)
   - Account records: remain in 'closed' status
   - Household: remains in 'closed' status
   - Audit trail: complete history preserved
   - Documents: retained per retention policy
```

### What the Client Experiences

| Day | Client's Experience |
|-----|-------------------|
| 0 | Goes to Fidelity, signs transfer paperwork |
| 1-2 | Nothing visible yet — paperwork is processing |
| 3-5 | May see "transfer in progress" at Fidelity |
| 5-8 | Positions start appearing at Fidelity |
| 8-10 | All assets at Fidelity, old accounts show $0 |
| 10+ | Old platform access becomes read-only or inaccessible |

The client doesn't need to do anything in THIS platform. The entire outbound process is triggered by the receiving firm (Fidelity) and processed through NSCC/ACATS. The current advisor and platform are reactive.

---

## Scenario C: Client Fires the Advisor but Doesn't Transfer

The client says: *"I'm revoking your advisory authority. Don't touch my accounts."*

This is rare but it happens. The client wants to keep their accounts at the custodian but manage them directly (or just do nothing for now).

```
What Happens
────────────
1. Client sends written notice revoking the Investment Advisory Agreement
   - This is a legal document, not a platform action
   - The firm's compliance team processes it

2. Firm admin/operations updates the platform:
   - Advisor relationship: status → 'ended'
   - Household may be reassigned to operations or a default advisor
   - Accounts may be restricted pending resolution

3. Advisory fees stop
   - Billing relationship is terminated
   - Final pro-rated invoice is generated

4. Accounts remain open at the custodian
   - But the advisor can no longer trade on them
   - The client would need to either:
     a) Self-direct at the custodian (if the custodian supports retail)
     b) Hire a new advisor
     c) Transfer accounts elsewhere

5. In this platform:
   - The household moves to a "no active advisor" state
   - Operations monitors it for resolution
   - Client records and account records remain
```

---

## What This Platform Handles vs. What It Doesn't

| Step | Who Handles It |
|------|---------------|
| Client's decision to leave | Outside the platform (phone call, email, letter) |
| Revoking advisory agreement | Legal process, recorded in the platform as relationship end |
| Inbound ACAT notification | Trading platform → Kafka → This platform consumes the event |
| Restricting accounts | This platform (account status → restricted) |
| Reviewing the ACAT | Operations team via this platform's task queue |
| Actual asset movement | NSCC/ACATS via the trading platform — this platform just tracks status |
| Closing accounts | This platform (after custodian confirms transfer complete) |
| Closing household | This platform (after all accounts are closed) |
| Final billing | This platform (pro-rated fee for partial period) |
| Record retention | This platform (nothing is deleted — regulatory requirement) |
| Ending advisor access | This platform (relationship ended → visibility removed) |

---

## The Key Safety Point

The client can always leave. Nothing in this platform can prevent it.

- The client's assets are at the **custodian**, not at the advisor's firm
- ACAT transfers are an **NSCC-regulated process** — the current custodian cannot indefinitely block them
- The advisory agreement can be revoked **at any time** by the client
- Once the advisor relationship ends, the advisor **immediately loses access** in this platform

The platform's job during a departure is:
1. **React** to the outbound transfer events
2. **Protect** the accounts during the transition (restrict trading)
3. **Close** cleanly once assets have moved
4. **Preserve** the full record for compliance and audit
5. **Bill** fairly for the partial period

---

## Status Transitions During Departure

### Account

```
active
  → restricted (acat_outbound)     ← ACAT notification received
    → closed                        ← transfer complete, balance $0
```

### Household

```
active
  → inactive                        ← all accounts restricted or closed
    → closed                        ← all accounts closed, no active relationships
```

### Advisor Relationship

```
active
  → ended (end_date set)            ← relationship terminated
```

### Billing

```
active billing schedule
  → final pro-rated invoice generated
    → billing relationship ended
```

---

## What's NOT in the Current Specs (Gaps)

The current epics cover the building blocks (account closure, household closure, ACAT transfers, advisor relationships, billing pro-ration) but there is no dedicated **client departure workflow** that orchestrates all of these together. Today, these would be handled as separate manual operations steps.

A future enhancement could be a **Departure Case** (similar to an Onboarding Case but in reverse):

```
Potential future: Departure Case Workflow

departure_initiated
  → accounts_restricted
    → acat_in_transit
      → acat_completed
        → final_billing
          → accounts_closed
            → household_closed
              → departure_complete (terminal)
```

This would tie all the departure steps into a single tracked workflow with the same audit trail, exception handling, and operational visibility that the onboarding case provides. Right now, the pieces exist but the orchestration wrapper doesn't.
