# Restricted automatic rules

Implemented as a separate module and approval flow. **Not enabled in the supplied
configuration or production.** Observation and recommendation policies never
implicitly become executable rules.

The six `baidu_ads_*_automatic_rule` tools create, revise, read, pause, simulate or
trigger a fixed rule. They cannot approve one. Only the primary owner with the
separate `policies:automate` scope may use this feature, and the owner must approve
the exact version/hash at the authenticated CSRF-protected
`/automatic-rules/{id}` page. Editing or pausing invalidates approval. The shipped
example does not grant that scope; `mutations_enabled` and
`automatic_mutations_enabled` are both false.

## Explicit authorization boundary

A rule contains one account, explicit numeric object IDs, one fixed action
(`pause`, `resume`, `set_campaign_budget`, or `set_manual_bid`), and an exact target
amount for a monetary action. The owner approves the cost/click trigger, maximum
observed cost, completed-date lookback, polling interval, cooldown, action counts
per run/day/total, expiry and mandatory stop conditions.

Monetary templates additionally require an explicit target cap, absolute amount
change per action/day/total and a budget-increase cap. No free-form model text,
arbitrary API request, wildcard object selector or unbounded target is executable.
Provider capabilities and their independent monetary limits still apply.

The scheduler polls at the declared interval, with **at most one immutable batch
per rule version per Beijing date**. Count and amount limits include reserved
batches, not merely successful writes. A repeated trigger returns its original
saved evidence, not new facts relabelled as the old evaluation. Previous unknown
outcomes stop later versions as well. Changing rule versions cannot reset the
rule's lifetime limits or remove unknown-result stops.

## Execution and stops

The system computes the completed window from a pinned release. Missing dates or
metrics, excessive observed cost, changed source revision during preparation,
stale identity, revoked scopes, exhausted limits, expiry or unknown prior outcome
prevent execution. Matching a cost threshold is not evidence of poor relevance,
wasted spend, lost conversions or improved ROI.

Each allowed batch creates a fixed plan whose authorization references the exact
approved rule/version/hash and evidence. It enters the existing execution queue;
that queue still verifies current requester/author/owner identities, approval,
capability, current provider state, idempotency and account locks. The rule guard
runs again before the actual provider POST. Scope reduction, a paused/revised rule,
switch changes, date rollover and exhausted budgets stop a queued action.

Provider results use the same readback/unknown/reconciliation handling as manual
plans. Automatic rules never resend an unknown mutation, restore an ad implicitly,
or bypass a later human change. Resuming an ad is a separate explicit action
template. Stop conditions do not promise to cancel a request already accepted by
the provider.

## Simulation and validation

Historical simulation writes no ad settings or execution jobs. It returns the
fixed proposed operations, source coverage, limits and suppressions. Historical
settings are not available for every date, so it uses the selected release's
settings snapshot and does not predict performance or revenue.

Synthetic tests cover authorization/version binding, complete coverage, monetary
and count limits, cooldown, identity/scope revocation, midnight rollover, unknown
results, cache revision changes, pause during identity I/O and disabled switches.
Real advertising accounts were never enabled or changed during this development.
