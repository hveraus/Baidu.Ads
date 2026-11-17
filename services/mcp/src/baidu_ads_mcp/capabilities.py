"""Persisted, explicit account/action contracts. Unknown capabilities deny writes."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import json
from .changes import canonical
from .query import ACCOUNTS, QueryError

SUPPORTED = frozenset({'set_campaign_budget','set_pause','set_manual_bid',
                       'add_negative_keywords','remove_negative_keywords','set_campaign_regions','set_campaign_schedule','set_campaign_devices','set_keyword_match','create_campaign','create_adgroup','create_keyword','create_creative','update_creative','delete_entity','set_campaign_bid_strategy'})


@dataclass(frozen=True)
class Capability:
    account: str
    operation: str
    kind: str
    adapter_id: str
    contract_version: str
    evidence_reference: str
    verified_until: float
    precondition_fields: tuple[str, ...]
    max_target_cny: str | None = None
    max_change_cny: str | None = None
    daily_change_cny: str | None = None
    allowed_values: dict[str, tuple[int, ...]] | None = None

    @property
    def money_fields(self):
        return ('campaignBid','campaignOcpcBid') if self.operation=='set_campaign_bid_strategy' else ((self.money_field,) if self.money_field else ())

    @property
    def money_field(self):
        if self.operation=='set_campaign_bid_strategy':return 'campaignBid'
        if self.operation in ('set_campaign_budget','create_campaign'):return 'budget'
        if self.operation=='create_adgroup':return 'maxPrice'
        if self.operation=='create_keyword':return 'price'
        if self.operation=='set_manual_bid':return 'price' if self.kind=='keyword' else 'maxPrice'
        return None

    def validate(self):
        if self.account not in ACCOUNTS or self.operation not in SUPPORTED:
            raise QueryError('UNSUPPORTED_OPERATION')
        kinds={'set_campaign_budget':{'campaign'},'set_pause':{'campaign','adgroup','keyword','creative'},
               'set_manual_bid':{'adgroup','keyword'},'add_negative_keywords':{'campaign','adgroup'},
               'remove_negative_keywords':{'campaign','adgroup'},'set_campaign_regions':{'campaign'},
               'set_campaign_schedule':{'campaign'},'set_campaign_devices':{'campaign'},'set_keyword_match':{'keyword'},'create_campaign':{'campaign'},'create_adgroup':{'adgroup'},'create_keyword':{'keyword'},'create_creative':{'creative'},'update_creative':{'creative'},'delete_entity':{'campaign','adgroup','keyword','creative'},'set_campaign_bid_strategy':{'campaign'}}
        if self.kind not in kinds[self.operation] or not self.precondition_fields:
            raise QueryError('INVALID_CAPABILITY')
        if self.operation=='delete_entity' and self.kind in ('campaign','adgroup'):
            # The SDK delete contract cascades to children but exposes no verified
            # subtree version guard. A complete approved subtree is required
            # before this can become an executable capability.
            raise QueryError('CASCADE_CONTRACT_UNVERIFIED')
        if not self.adapter_id or not self.contract_version or not self.evidence_reference:
            raise QueryError('UNVERIFIED_CAPABILITY')
        if self.operation in ('set_campaign_regions','set_campaign_devices','set_keyword_match','create_keyword','set_campaign_bid_strategy') and not self.allowed_values:
            raise QueryError('VERIFIED_ENUM_VALUES_REQUIRED')
        if self.money_field:
            for value in (self.max_target_cny,self.max_change_cny,self.daily_change_cny):
                try:
                    amount=Decimal(value)
                    if not amount.is_finite() or amount<=0:raise InvalidOperation()
                except (TypeError,InvalidOperation):raise QueryError('MONEY_LIMITS_REQUIRED') from None
            if not self.operation.startswith('create_') and any(field not in self.precondition_fields for field in self.money_fields):
                raise QueryError('INVALID_CAPABILITY')


class CapabilityRegistry:
    def __init__(self,store):
        self.store=store
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS execution_capabilities(account TEXT,op TEXT,kind TEXT,body TEXT,enabled INTEGER,PRIMARY KEY(account,op,kind))')

    def record(self,owner,capability: Capability,enabled=False):
        """Trusted administrator API, intentionally not an MCP tool. Never auto-enable."""
        if owner.subject!='kenshi' or owner.role!='owner':raise QueryError('INSUFFICIENT_ROLE')
        owner.require('changes:execute',capability.account)
        if capability.operation=='delete_entity':owner.require('changes:delete',capability.account)
        capability.validate()
        if capability.verified_until<=self.store.clock():raise QueryError('CAPABILITY_EXPIRED')
        with self.store.db() as db:
            db.execute('INSERT OR REPLACE INTO execution_capabilities VALUES(?,?,?,?,?)',
                       (capability.account,capability.operation,capability.kind,canonical(asdict(capability)),int(enabled)))

    def require(self,db,account,operation,kind,adapter_id):
        row=db.execute('SELECT body,enabled FROM execution_capabilities WHERE account=? AND op=? AND kind=?',
                       (account,operation,kind)).fetchone()
        if not row or not row['enabled']:raise QueryError('UNVERIFIED_CAPABILITY')
        data=json.loads(row['body']);data['precondition_fields']=tuple(data['precondition_fields'])
        capability=Capability(**data);capability.validate()
        if capability.verified_until<=self.store.clock():raise QueryError('CAPABILITY_EXPIRED')
        if capability.adapter_id!=adapter_id:raise QueryError('ADAPTER_CONTRACT_MISMATCH')
        return capability

    def describe(self,account):
        with self.store.db() as db:
            rows=db.execute('SELECT body,enabled FROM execution_capabilities WHERE account=?',(account,)).fetchall()
        return [{'contract':json.loads(r['body']),'enabled':bool(r['enabled']),
                 'expired':json.loads(r['body'])['verified_until']<=self.store.clock()} for r in rows]
