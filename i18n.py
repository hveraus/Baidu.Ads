"""Build the English UI locally while preserving all source advertising data."""
import json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
# These are data comparisons, not display labels. Never translate them.
PROTECTED=["r.device==='移动设备'","r.device==='移动'","x.name==='文档管理系统'","/文件.*管理|管理.*文件|档案/"]
ENUMS={'计算机':'Desktop','移动设备':'Mobile','移动':'Mobile','精确':'Exact','短语':'Phrase','智能匹配':'Smart match','智能':'Smart','自动定向':'Automatic targeting','词包':'Keyword package','北京':'Beijing','天津':'Tianjin','河北':'Hebei','山西':'Shanxi','内蒙古':'Inner Mongolia','辽宁':'Liaoning','吉林':'Jilin','黑龙江':'Heilongjiang','上海':'Shanghai','江苏':'Jiangsu','浙江':'Zhejiang','安徽':'Anhui','福建':'Fujian','江西':'Jiangxi','山东':'Shandong','河南':'Henan','湖北':'Hubei','湖南':'Hunan','广东':'Guangdong','广西':'Guangxi','海南':'Hainan','重庆':'Chongqing','四川':'Sichuan','贵州':'Guizhou','云南':'Yunnan','西藏':'Tibet','陕西':'Shaanxi','甘肃':'Gansu','青海':'Qinghai','宁夏':'Ningxia','新疆':'Xinjiang','香港':'Hong Kong','澳门':'Macao','台湾':'Taiwan','其他':'Other','未知':'Unknown'}
def english_template(source):
 mapping=json.loads((ROOT/'i18n.en.json').read_text())
 for i,s in enumerate(PROTECTED):source=source.replace(s,f'__BAIDU_RAW_{i}__')
 pattern=re.compile('|'.join(map(re.escape,sorted(mapping,key=len,reverse=True))))
 source=pattern.sub(lambda m:mapping[m[0]],source)
 for i,s in enumerate(PROTECTED):source=source.replace(f'__BAIDU_RAW_{i}__',s)
 source=source.replace('lang="zh-CN"','lang="en"').replace("window.REPORT_LANG='zh'","window.REPORT_LANG='en'")
 source=source.replace("toLocaleString('zh-CN'","toLocaleString('en-US'")
 source=source.replace('。','. ').replace('；','; ').replace('：',': ').replace('，',', ')
 source=source.replace('period.The latest','period. The latest')
 source=source.replace("const filtered=name=>",'const displayLabel=v=>('+json.dumps(ENUMS,ensure_ascii=False)+')[v]||String(v);\nconst filtered=name=>')
 source=source.replace('String(r.device));bars', 'displayLabel(r.device));bars')
 source=source.replace('r=>r.wMatchId);bars','r=>displayLabel(r.wMatchId));bars')
 source=source.replace('r=>r.provinceName);bars','r=>displayLabel(r.provinceName));bars')
 return source
