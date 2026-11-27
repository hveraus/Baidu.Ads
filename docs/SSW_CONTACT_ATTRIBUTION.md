# SSW 联系目标来源统计

SSW 中国站的联系我们目标按会话计数，同一会话最多一次，不代表提交询盘或成交。日期使用 Asia/Shanghai；每日 09:00 采集完整日期，实时当天数据不混入完整日报。

## 两组来源

- 百度广告：百度统计全部来源报告中 `source=searchBaiduProFc` 标记的百度付费搜索会话，且触发联系我们目标。
- 自然流量：按照业务口径，除百度广告外的全部来源合并，包括直接访问、外链及自然搜索，不等同于 SEO 自然搜索。
- 全部联系目标 = 百度广告联系目标 + 自然流量联系目标。平台来源判定是统计依据，无法恢复被浏览器或跳转丢失的来源信息，也不做关键词转化归因。

## 查询与采集

`scripts/sync_contact_attribution.py` 复用 ECS 已有的百度统计读凭据及 SSW 站点配置。调用官方 `ReportService/getData`，使用 `method=source/all/a`、`viewType=type`、`metrics=visit_count,trans_count`、配置中的 `contact_click_target`、`max_results=0`，逐日读取并与完整区间汇总对账。无需向 Agent 提供凭据，也无需每次登录远程桌面。

生产问题定位中发现原 `pro/hour/a` 返回空值，而全部来源报告有可用的百度付费搜索记录；因此 SSW 使用来源分组汇总，TinaCMS 原有口径不变。不得把百度自然搜索 `searchBaiduNature` 当作广告。

推广趋势日报返回 `--` 或没有数值时，采集日志会记录该情况。若「全部来源」归因已覆盖本轮目标日期，看板不会因此告警。若归因也未更新，看板保留上次成功结果，标明实际覆盖日期并告警。接口调用异常仍按采集失败处理。

快照 `accounts/ssw/data/website-contact-attribution.json` 仅保存在 ECS，包含生成时间、覆盖日期及每日聚合数，不包含访客资料。该路径被 Git 忽略。采集失败保留上次快照，不能发布半成品。

`--` 先保留未知；只有完整来源分组或完整日期区间的明确数值总计与已知非负计数相等时，才能通过对账证明剩余值为零。分页不全、未知来源代码或总量不一致会阻止更新。

## 看板和 MCP

双语 SSW 总览显示“联系我们点击来源”：全部、百度广告、自然流量三项，以及各自覆盖天数。不随推广计划筛选改变。

`baidu_ads_get_website_metrics` 使用 `account=ssw`、`traffic_scope=baidu_paid`、明确的 `start` 和 `end` 查询同一快照，同时返回：

- `paid_contact_click_sessions`：百度广告联系目标。
- `natural_contact_click_sessions`：其余来源联系目标。
- `contact_click_sessions`：全部来源联系目标。

缺少任一请求日期时，MCP 的对应区间合计为 null，并列出缺失日期；看板按已有日期显示累计并注明覆盖。获取最新完整日期可先查询 MCP 数据状态，必要时刷新其缓存；刷新缓存不会触发核心采集。

官方依据：[全部来源报告](https://tongji.baidu.com/api/manual/Chapter1/source_all_a.html)、[推广趋势报告](https://tongji.baidu.com/api/manual/Chapter1/pro_hour_a.html)。
