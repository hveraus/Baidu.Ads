import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from account_ui import account_template
from i18n import english_template
from publication import public_template
class AccountUITests(unittest.TestCase):
 def test_yakshaver_has_own_advice_identity_and_review_state(self):
  source=public_template((ROOT/'dashboard.template.html').read_text())
  profile={'slug':'yakshaver','name':'YakShaver','user_name':'智鹏瑞尔-1'}
  for language,template in [('zh',source),('en',english_template(source))]:
   rendered=account_template(template,profile,language)
   self.assertIn('YakShaver',rendered)
   self.assertIn('智鹏瑞尔-1',rendered)
   self.assertNotIn('TinaDocs',rendered)
   self.assertNotIn('www.tinacms.com.cn',rendered)
   self.assertEqual(rendered.count('function advice()'),1)
   self.assertIn('baidu-ads-switch-state-yakshaver',rendered)
   self.assertIn('/*__DATA__*/',rendered)
   self.assertIn('account-evidence',rendered)
   self.assertIn('../yakshaver/',rendered)
 def test_tina_advice_is_preserved_and_data_not_renamed(self):
  source=(ROOT/'dashboard.template.html').read_text()
  rendered=account_template(source,{'slug':'tinacms','name':'TinaCMS'},'zh')
  self.assertIn('TinaDocs',rendered)
  self.assertIn('baidu-ads-switch-state-tinacms',rendered)
  # Branding is applied before source data, never by replacing strings in the payload.
  raw=json.dumps({'keyword':'TinaCMS YakShaver 原始关键词'},ensure_ascii=False)
  result=account_template(source,{'slug':'yakshaver','name':'YakShaver','user_name':'other'},'zh').replace('/*__DATA__*/',raw)
  self.assertIn(raw,result)
if __name__=='__main__':unittest.main()
