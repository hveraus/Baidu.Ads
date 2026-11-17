# Advertising execution contracts

The HTTP service and the independent worker use `runtime.build_execution_queue`.
Constructing the queue performs no advertising API call. Both deployment switches
default to false, and construction does not register or enable capabilities.
No live advertising write or account write entitlement was tested during development.

## Implemented operations

| Draft operation | Official Search API contract | Execution constraints |
| --- | --- | --- |
| `set_campaign_budget` | `CampaignService/updateCampaign`, `budget` | Reject shared budgets; amount and daily change limits |
| `set_pause` | `updateCampaign`, `updateAdgroup`, `updateWord`, `updateCreative`, `pause` | Explicit campaign, adgroup, keyword or creative ID |
| `set_manual_bid` | `updateAdgroup/maxPrice`, `updateWord/price` | Recheck parent campaign; operator-verified manual modes only |
| `add_negative_keywords`, `remove_negative_keywords` | `updateCampaign`, `updateAdgroup`, `negativeWords` or `exactNegativeWords` | Replace verified full list while preserving other source words |
| `set_campaign_regions` | `updateCampaign/regionTarget` | Explicit verified regional integer codes |
| `set_campaign_schedule` | `updateCampaign/schedule` | Validated non-overlapping **paused** intervals; empty means no paused intervals |
| `set_campaign_devices` | `updateCampaign/equipmentType` | Explicit verified account enum values |
| `set_keyword_match` | `updateWord/matchType,phraseType` | Explicit verified account enum values |
| `set_campaign_bid_strategy` | `updateCampaign/campaignBidType,campaignOcpcBidType,campaignBid,campaignOcpcBid` | Verified mode enum sets and independent price/daily limits; no invented named-strategy mappings |
| `create_campaign` | `CampaignService/addCampaign` | Paused; bounded budget; returned campaign ID required |
| `create_adgroup` | `AdgroupService/addAdgroup` | Paused; existing campaign ID; parent reread |
| `create_keyword` | `KeywordService/addWord` | Paused; existing adgroup ID; verified match enums; parent reread |
| `create_creative` | `CreativeService/addCreative` | Paused; existing adgroup ID; strict text and HTTP(S) destination URLs |
| `update_creative` | `CreativeService/updateCreative` | Existing ID; explicit text and destination URL fields |
| `delete_entity` | `deleteCampaign`, `deleteAdgroup`, `deleteWord`, `deleteCreative` | Separate irreversible acknowledgement and owner deletion scope; keyword/creative require full approved stable-object reread. Campaign/adgroup execution is blocked without a verified subtree concurrency contract. |

Requests use narrow typed models and fixed official URLs, with no raw endpoint
forwarding. Unknown enums remain disabled until a capability records the verified
values for that account. SDK field presence proves request construction, not
live provider authorization, business validation, or account-specific eligibility.

The wire models were checked against Baidu's official SDK revision
[`ea0d5748d530f8b79c1f587f05eb92579be485f4`](https://github.com/baidu/baiduads-sdk/tree/ea0d5748d530f8b79c1f587f05eb92579be485f4/python/baiduads-sdk-auto):
[CampaignService](https://github.com/baidu/baiduads-sdk/blob/ea0d5748d530f8b79c1f587f05eb92579be485f4/python/baiduads-sdk-auto/baiduads/campaign/api/campaign_service.py),
[AdgroupService](https://github.com/baidu/baiduads-sdk/blob/ea0d5748d530f8b79c1f587f05eb92579be485f4/python/baiduads-sdk-auto/baiduads/adgroup/api/adgroup_service.py),
[KeywordService](https://github.com/baidu/baiduads-sdk/blob/ea0d5748d530f8b79c1f587f05eb92579be485f4/python/baiduads-sdk-auto/baiduads/keyword/api/keyword_service.py),
[CreativeService](https://github.com/baidu/baiduads-sdk/blob/ea0d5748d530f8b79c1f587f05eb92579be485f4/python/baiduads-sdk-auto/baiduads/creative/api/creative_service.py).

## Private runtime and executable verification

Install the MCP package from its own lock as described in README.md. Run a fully
local disposable queue/approval/provider/readback scenario:

```sh
python -m baidu_ads_mcp.worker --simulate
```

Expected output has `state: succeeded`, `provider_calls: 1`, and
`production_calls: 0`. The demonstration uses synthetic data and never accesses
provider credentials or a network endpoint.

The actual worker is independently runnable:

```sh
python -m baidu_ads_mcp.worker --config /private/path/mcp.json --limit 10
```

The worker prefers `cache_directory/current.json` from the MCP-owned cache. It
validates the MCP marker, all three report hashes, revision and original fetch
expiry; restarting cannot extend that expiry. Corrupt, linked or expired cache
state stops the worker. Only an absent or empty cache can use the explicitly
configured `release_copy`, which also supports standalone development.
HTTP startup may load an otherwise valid expired cache solely to keep sign-in
and authenticated refresh available. Its original timestamp is preserved, all
data-query tools still reject it as `SNAPSHOT_EXPIRED`, and a complete validated
refresh must replace it before data queries recover. This exception does not
allow corrupt hashes, invalid pointers or expired worker execution.
`mutations_enabled` and `automatic_mutations_enabled` default to false and are
reread before submission. Only independent `MCP_BAIDU_WRITE_CREDENTIALS_JSON`
is used for provider credentials: an account-keyed object containing `user_name`
and `access_token`. Do not use the collector's configuration, repository files,
public reports, logs, or shell command arguments for secrets. Manual-bid execution
also needs `verified_manual_bid_modes`, mapping each account to verified integer
modes. No capability is inferred from that setting.

Both runtimes resolve identities using the same OAuth provider `resolve_actor`,
checking token family revocation, original scopes, client registration, current
grant versions and the live identity bridge. Synchronous queue methods must run
inside `asyncio.to_thread` in HTTP code. The requester, original author and owner
approver must remain authorized and unchanged. Automatic-rule plans additionally
run the rule guard at enqueue, intent validation and immediately before submit.

## Persistence and recovery

Each approved plan/version has one durable job and a bound idempotency key.
The intent is committed before the first API submission. Each individual HTTP
request has an absolute 20-second cancellation deadline and no internal retry.
Identity resolution has a five-second overall deadline. A compound manual-bid
reread, final authorization and submit can take at most 80 seconds; the default
lease is 90 seconds. Production execution and recovery must go through `tick`,
which owns an OS worker lock for the complete operation. A paused living worker
cannot be taken over just because its lease expires. Late receipts cannot replace
a newer claim's results. This is not upstream exactly-once delivery or a provider
compare-and-swap guarantee; edits in the provider's UI can still race the last read.

An interrupted or timed-out write enters `unknown`. No automatic retry sends the
same intent again. Recovery performs reads only, and observing an old value is not
proof a delayed write cannot apply. Unknown jobs retain a conservative account
lock, blocking parent and child changes until reconciliation is authoritative.
New jobs and explicit changed fields are bounded; zero-change proposals are rejected.

Creation stores the exact new ID returned by the successful API response. Parent
and child creation are separate stages: approve a parent creation, reconcile its
returned ID, then draft a child using that known ID. There is no blind dependency
resolution by request IDs, names, or order within a plan.

If an add response is lost, the job requires creation-provenance reconciliation.
The owner can record a candidate ID and evidence, but matching fields or a name
never proves the object belongs to this intent. The Baidu adapter currently has
no verified provider audit endpoint linking such a candidate to this intent;
therefore the candidate remains `unknown` and the account remains blocked.

Compensation creates a new unapproved draft only if the latest snapshot still
matches the confirmed result. It does not erase spend or exposures and cannot
reverse deletion. An unchanged paused creation needs no pause compensation.
Later human edits require a separate explicit new proposal rather than automatic
restoration. Creative updates that are still under provider review remain unknown
until the exact base object reflects the requested fields; no approval is invented.

## Remaining external contract gaps

* No production account's write entitlement, enum semantics or limits were enabled
  or validated by a real write. These require separately reviewed evidence.
* Campaign/adgroup cascade deletion cannot execute without a verified entire-subtree
  concurrency guard; the official SDK has no verified conditional version contract.
* Creation timeouts cannot be resolved by owner assertion alone until an upstream
  audit/provenance API is verified. Safe readback recovery is implemented and fails closed.
* The selected smart-bid field contract is implemented. Portfolio/shared-budget
  management and provider-specific conversion-goal/ocpc package creation are not
  invented or routed through these fields; they need their own verified contracts.
* This adapter covers standard Search text creatives, not every Baidu advertising
  product, image/video creative type, or provider editorial workflow.
