from baidu_ads_mcp.query import QueryError
from baidu_ads_mcp.capabilities import Capability,CapabilityRegistry
from test_analytics_changes import Base,OPERATOR,OWNER


class ExtendedOperationsTests(Base):
    def setUp(self):
        super().setUp()
        campaign=self.query.data['tinacms']['extra']['inventory']['campaign'][0]
        campaign.update(regionTarget=[1000],equipmentType=3)
        for keyword in self.query.data['tinacms']['extra']['inventory']['keyword']:
            keyword.update(matchType=1,phraseType=1)
    def prepare(self,op):return self.store.create(OPERATOR,'tinacms',[op],'synthetic proposal')['proposal']['changes'][0]
    def test_regions_preserve_codes_and_deduplicate(self):
        change=self.prepare({'op':'set_campaign_regions','entity_id':'1','region_ids':[2000,2000]})
        self.assertEqual(change['before']['regionTarget'],[1000]);self.assertEqual(change['after']['regionTarget'],[2000])
    def test_schedule_is_explicit_paused_intervals(self):
        change=self.prepare({'op':'set_campaign_schedule','entity_id':'1','paused_intervals':[]})
        self.assertEqual(change['after']['schedule'],[])
        for intervals in ([{'weekDay':1,'startHour':12,'endHour':10}],
                          [{'weekDay':1,'startHour':1,'endHour':5},{'weekDay':1,'startHour':3,'endHour':7}]):
            with self.assertRaisesRegex(QueryError,'INVALID_OPERATION'):
                self.prepare({'op':'set_campaign_schedule','entity_id':'1','paused_intervals':intervals})
    def test_device_and_match_diffs(self):
        change=self.prepare({'op':'set_campaign_devices','entity_id':'1','equipment_type':1})
        self.assertEqual(change['changed_fields']['equipmentType'],{'before':3,'after':1})
        change=self.prepare({'op':'set_keyword_match','entity_id':'9','match_type':2,'phrase_type':2})
        self.assertEqual(change['after']['matchType'],2);self.assertEqual(change['after']['phraseType'],2)
    def test_enum_capabilities_require_verified_values(self):
        registry=CapabilityRegistry(self.store)
        value=Capability('tinacms','set_campaign_devices','campaign','test','v1','synthetic',5000,('equipmentType',))
        with self.assertRaisesRegex(QueryError,'VERIFIED_ENUM_VALUES_REQUIRED'):registry.record(OWNER,value,enabled=True)
