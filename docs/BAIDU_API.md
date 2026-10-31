# 百度营销 API 接入与续期

## 获取授权

1. 使用自己的推广账号登录 [百度营销商业开发者中心](https://dev2.baidu.com/appmanage)。当前应用名为 `tina-tracker`，类型为客户应用。
2. 在应用详情的自授权信息中获取已有令牌，并记录界面显示的到期日。获取的是自己推广账号的数据访问凭据；不要把它放进网页 JS 或 Git。
3. 本机首次配置运行 `python scripts/configure.py`。令牌使用隐藏输入，保存到被 Git 忽略的 `.credentials.json`。
4. 也可用 `BAIDU_CREDENTIALS_JSON` 环境变量提供相同 JSON；其优先级高于本机文件。GitHub Actions 使用 Repository Secret 提供该变量。
5. 用账户实际登录名而非仅凭显示名称。`user_id`、`app_id` 按自己的后台记录填写。不要从 JWT 内部字段推断界面授权到期日。

这是自授权流程，当前采集不需要 OAuth 回调网站。若未来改为第三方账户授权，应实现百度官方 OAuth 回调、校验 state/签名，交换与刷新令牌，而不是直接把网站首页当作已实现的回调接口。

## 请求结构

```json
{
  "header": {"userName": "YOUR_ACCOUNT", "accessToken": "FROM_SECRET_STORE"},
  "body": {
    "reportType": 2602783,
    "startDate": "2026-10-01",
    "endDate": "2026-10-28",
    "timeUnit": "DAY",
    "columns": ["date", "wInfoId", "wInfoNameStatus", "impression", "click", "cost"],
    "startRow": 0,
    "rowCount": 1000,
    "needSum": false
  }
}
```

报告地址：`POST https://api.baidu.com/json/sms/service/OpenApiReportService/getReportData`。JSON Content-Type 使用 `application/json;charset=utf-8`。请求字段和 16 个报告配置在 `config.json`。

## 只读接口范围

| 接口 | 用途 |
|---|---|
| `AccountService/getAccountInfo` | 账户验证 |
| `OpenApiReportService/getReportData` | 表现报告 |
| `CampaignService/getCampaign` | 计划、否定词、时段与预算快照 |
| `AdgroupService/getAdgroup` | 单元清单与设置 |
| `KeywordService/getWord` | 全部当前关键词及影子、质量度、状态、网址 |

关键词清单必须同时查询本体 `getTemp=0` 与影子 `getTemp=1`；它们不能直接相加当作关键词数。关键词表现和实际搜索词是不同报告。

## 报告类型

| 报告 | reportType |
|---|---|
| 账户 | 2208157 |
| 计划 | 2290316 |
| 单元 | 2284618 |
| 关键词、关键词设备/地域/匹配/小时/转化/竞争位置 | 2602783 |
| 实际搜索词 | 2307838 |
| 创意 | 2665235 |
| 落地页 | 5381679 |
| 多渠道 | 1783967 |
| IP 计费 / 无效点击 | 107181 |

采集以不超过 30 天的请求段读取，自动分页；空页但总数未读完、重复分页或 API 非成功状态都会报错。完整抓取后才替换对应日期段，保留其他历史。

## 续期

当前登记自授权有效期是 2027-01-25；以自己的百度界面为准。到期前在同一应用详情续期，安全更新本机凭据，再运行 `python scripts/configure_github.py` 更新 Secret。脚本会在到期前 14 天标记提醒；不会悄悄更改授权或绕过续期。

不要重置令牌，除非你确实需要使旧令牌失效。若令牌曾意外公开，应立即在百度后台撤销/重置，并更新 Secrets 与恢复备份。

## 官方依据

- [关键词报告](https://dev2.baidu.com/content?sceneType=0&pageId=102476&nodeId=705&subhead=)
- [搜索词报告](https://dev2.baidu.com/content?sceneType=0&pageId=102477&nodeId=704&subhead=)
- [IP 计费报告](https://dev2.baidu.com/content?sceneType=0&pageId=102665&nodeId=732&subhead=)
- [转化指标](https://dev2.baidu.com/content?sceneType=0&pageId=104180&nodeId=1163&subhead=)
- [OAuth 说明](https://dev2.baidu.com/content?sceneType=0&pageId=100441&nodeId=421&subhead=)
