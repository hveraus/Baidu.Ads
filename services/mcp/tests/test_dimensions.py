import json
import tempfile
from pathlib import Path
import unittest
from baidu_ads_mcp.query import ReleaseQuery,QueryError
from fixtures import make_release

class DimensionTests(unittest.TestCase):
    def test_hour_and_creative_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            release=make_release(folder)
            for path in release.rglob('*.html'):
                data=json.loads(path.read_text().split('>',1)[1].split('</script>')[0])
                data['reports']['account_hour']=[{'date':'2026-11-14 09:00','hour':9,'cost':'1.23','click':1,'impression':5}]
                data['reports']['creative']=[{'date':'2026-11-14','campaignId':1,'adGroupId':2,'ideaId':3,'ideaInfo':{'ideaTitle':'原广告文案','unexpected_secret':'not-retained'},'cost':'1.23','click':1,'impression':5}]
                path.write_text('<script id="report-data" type="application/json">'+json.dumps(data)+'</script>')
            query=ReleaseQuery(release)
            hourly=query.performance('tinacms','2026-11-14','2026-11-14','account_hour')
            self.assertEqual(hourly['items'][0]['entity']['hour'],9)
            self.assertEqual(hourly['observed_totals']['cost'],'1.230000')
            ad=query.performance('tinacms','2026-11-14','2026-11-14','creative')
            self.assertEqual(ad['items'][0]['entity']['ideaInfo'],{'ideaTitle':'原广告文案'})
            path=release/'report/index.html'
            path.write_text(path.read_text().replace('09:00','25:00'))
            with self.assertRaisesRegex(QueryError,'INVALID_SNAPSHOT'):ReleaseQuery(release)

    def test_filters_sort_and_cursor_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            query=ReleaseQuery(make_release(folder))
            first=query.performance('tinacms','2026-11-14','2026-11-14','campaign',limit=1,descending=False)
            self.assertEqual(str(first['items'][0]['entity']['campaignId']),'1')
            with self.assertRaisesRegex(QueryError,'INVALID_CURSOR'):
                query.performance('tinacms','2026-11-14','2026-11-14','campaign',limit=1,cursor=first['next_cursor'],descending=True)
            selected=query.performance('tinacms','2026-11-14','2026-11-14','campaign',filters={'campaignId':'2'})
            self.assertEqual(selected['total_count'],1)
            self.assertEqual(str(selected['items'][0]['entity']['campaignId']),'2')
            filtered=query.performance('tinacms','2026-11-14','2026-11-14','campaign',filters={'campaignName':'同名'},limit=1)
            next_page=query.performance('tinacms','2026-11-14','2026-11-14','campaign',filters={'campaignName':'同名'},limit=1,cursor=filtered['next_cursor'])
            self.assertNotEqual(filtered['items'][0]['entity']['campaignId'],next_page['items'][0]['entity']['campaignId'])
            with self.assertRaisesRegex(QueryError,'INVALID_FILTER'):
                query.performance('tinacms','2026-11-14','2026-11-14','campaign',filters={'url':'https://example.com'})
