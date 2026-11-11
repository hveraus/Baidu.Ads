# TinaCMS 中文站转化统计

总览中的「网站转化率」跟随日期筛选，按北京时间汇总 `tinacms.com.cn` 全站会话。两项目标分别按会话去重，计算为「发生目标行为的会话数 ÷ 同期网站会话数」。广告计划筛选不影响这两项数据。部分日期有数据时，分子与分母都只取同一批已覆盖日期，并显示覆盖天数。已测得零次且分母大于零时显示 `0.00%`，缺少数据或分母为零时显示 `—`。

## 官网埋点

百度统计站点「TinaCMS 中国站」已创建，站点 ID 为 `23683692`。[获取站点统计代码](https://tongji.baidu.com/main/setting/90172887/home/site/getjs?siteId=23683692)

在 `www.tinacms.com.cn` 安装该站点的统计代码，沿用官网的统计授权设置。确认裸域跳转到 `www`，并验证站内导航能记录页面浏览。

两个目标已在百度统计配置为「页面转化」，记录方式为「按访问次数」。后台已停止新增旧版 Trackevent 转化目标，改用 `_trackPageview` 记录虚拟目标页。虚拟浏览会计入百度统计的 PV，并在同一会话中对每个目标去重。[基本转化设置](https://tongji.baidu.com/main/setting/90172887/home/trans/basicsetting?siteId=23683692&flag=page) · [虚拟页面跟踪](https://tongji.baidu.com/web/help/article?id=235&type=0)

| 目标 | 触发点 | 虚拟路径 |
|---|---|---|
| 安装命令复制成功 | 首页的 `npx create-tina-app@latest` 成功写入剪贴板后 | `/__analytics/conversion/install-copy-success` |
| 邮件咨询点击 | 首页、定价页等咨询入口的 `mailto:` 链接被点击，保留默认打开邮件客户端的行为 | `/__analytics/conversion/email-click` |

可将 `integrations/tinacms/tongji-events.mjs` 集成到官网：在复制成功分支调用 `trackInstallCopySuccess(label)`；在首页和定价页咨询链接的点击回调调用 `trackEmailClick(event.currentTarget.href)`。一个入口只接一次回调。模块只负责事件调用，基础统计代码与授权须由官网接入。两项目标不证明安装已运行或邮件已发送。

联调时检查成功复制、复制失败、复制其他内容和邮件链接的行为。同一会话各触发两次，核对百度报表每项目标只计一个会话。首个纳入计算的日期为官网开始稳定采集后的首个完整自然日。

## 同步到看板

账户的数据 API 尚待开通。百度后台要求阅读并同意《API服务系统保密协议》，官方说明列出的开通条件包括自有网站昨日 PV 超过 100。[开通说明](https://tongji.baidu.com/web/help/article?id=123&type=0)

百度商业账号可使用[趋势分析](https://tongji.baidu.com/api/manual/Chapter1/trend_time_a.html)读取每日 `visit_count` 和每个目标的 `trans_count`。先在实际站点核对两个目标 ID 能分别查询且计数与后台一致，再通过 GitHub Secret `BAIDU_TONGJI_CREDENTIALS_JSON` 启用每日同步。其 JSON 字段为 `user_name`、`access_token`、整数 `site_id`、整数 `install_copy_target`、整数 `email_click_target`、`start_date`（`YYYY-MM-DD`）。使用具有百度统计读取权限的令牌；百度推广报表权限不代表已有站点统计权限。本站点必须出现在授权账户的站点列表中，且 ID 对应 `tinacms.com.cn` 或 `www.tinacms.com.cn`。

本地可用同名环境变量运行 `python scripts/sync_website_conversions.py`。同步读取截至北京时间昨天的数据，并重新查询最近一周。同步失败时每日采集保留上一份网站快照，广告报表继续更新。该接口只读取数据，不修改推广设置。

也可以将有权限的统计后台导出整理成如下 UTF-8 CSV。导入同一日期会替换该日记录；空白目标列表示未测，`0` 表示确认是零。每天一行，访问和转化会话采用相同的日期归属规则与流量范围。

```csv
date,sessions,install_copy_sessions,email_click_sessions
```

```sh
python scripts/sync_website_conversions.py --csv /path/to/daily-aggregates.csv
python dashboard.py --public
```

同步和导入只向 `data/website-conversions.json` 写入日期与整数会话汇总。原始访问明细、邮箱地址、点击标识及统计凭据不进入公开数据。生成的中英文看板位于 `report/index.html` 与 `report/en.html`。
