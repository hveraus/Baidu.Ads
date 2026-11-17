"""Strict creation/creative/delete contracts; no implicit live authorization."""
from typing import Annotated,Literal
from urllib.parse import urlsplit
from pydantic import Field,model_validator,BaseModel,ConfigDict
class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
EntityID=Annotated[str,Field(pattern=r'^[0-9]{1,24}$')]
Money=Annotated[str,Field(pattern=r'^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$')]

RequestID=Annotated[str,Field(pattern=r'^[0-9a-f]{32}$')]
Label=Annotated[str,Field(min_length=1,max_length=200)]


class CreateCampaign(Strict):
    op: Literal['create_campaign']
    kind: Literal['campaign']='campaign'
    request_id: RequestID
    name: Label
    budget: Money
    pause: Literal[True]=True


class CreateAdgroup(Strict):
    op: Literal['create_adgroup']
    kind: Literal['adgroup']='adgroup'
    request_id: RequestID
    campaign_id: EntityID
    name: Label
    max_price: Money
    pause: Literal[True]=True


class CreateKeyword(Strict):
    op: Literal['create_keyword']
    kind: Literal['keyword']='keyword'
    request_id: RequestID
    adgroup_id: EntityID
    keyword: Label
    price: Money
    match_type: int=Field(ge=0,le=2147483647)
    phrase_type: int|None=Field(default=None,ge=0,le=2147483647)
    pause: Literal[True]=True


class CreativeFields(Strict):
    title: str=Field(min_length=1,max_length=200)
    description1: str=Field(min_length=1,max_length=500)
    description2: str=Field(default='',max_length=500)
    pc_destination_url: str=Field(min_length=1,max_length=2048)
    mobile_destination_url: str=Field(min_length=1,max_length=2048)
    @model_validator(mode='after')
    def urls(self):
        for value in (self.pc_destination_url,self.mobile_destination_url):
            parsed=urlsplit(value)
            if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('HTTP(S) destination URL required, without credentials')
        return self


class CreateCreative(CreativeFields):
    op: Literal['create_creative']
    kind: Literal['creative']='creative'
    request_id: RequestID
    adgroup_id: EntityID
    pause: Literal[True]=True


class UpdateCreative(CreativeFields):
    op: Literal['update_creative']
    kind: Literal['creative']='creative'
    entity_id: EntityID


class DeleteEntity(Strict):
    op: Literal['delete_entity']
    kind: Literal['campaign','adgroup','keyword','creative']
    entity_id: EntityID
    acknowledge_irreversible: Literal[True]


def creation_target(operation):
    op=operation['op']
    if op=='create_campaign':return {'campaignName':operation['name'],'budget':operation['budget'],'pause':True}
    if op=='create_adgroup':return {'campaignId':operation['campaign_id'],'adgroupName':operation['name'],'maxPrice':operation['max_price'],'pause':True}
    if op=='create_keyword':
        value={'adgroupId':operation['adgroup_id'],'keyword':operation['keyword'],'price':operation['price'],'matchType':operation['match_type'],'pause':True}
        if operation['phrase_type'] is not None:value['phraseType']=operation['phrase_type']
        return value
    result={key:operation[src] for key,src in (('title','title'),('description1','description1'),('description2','description2'),('pcDestinationUrl','pc_destination_url'),('mobileDestinationUrl','mobile_destination_url'))}
    if op=='create_creative':result.update(adgroupId=operation['adgroup_id'],pause=True)
    return result


def object_identity(operation):
    return operation.get('kind','campaign'),operation.get('entity_id','new:'+operation.get('request_id',''))


def parent_preconditions(account,operation,parent=None):
    if operation['op']=='create_campaign':return {'account':account}
    if parent is None:return None
    result={'parentId':str(parent['campaignId'] if operation['op']=='create_adgroup' else parent['adgroupId'])}
    for key in ('pause','status'):
        if key in parent:result['parent'+key.capitalize()]=parent[key]
    return result
