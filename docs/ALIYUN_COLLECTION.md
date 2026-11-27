# 阿里云本地采集和存储

在 ECS 上以现有看板和微信客服计划任务的 Windows 账号操作。看板使用独立的采集 checkout，避免生产数据被代码升级覆盖。Windows 使用中国标准时间，计划任务每天 09:00 运行。

1. 从 GitHub 克隆已验证的迁移版本到 `%USERPROFILE%\baidu-ads-collector`。首次迁移用同一版本带来的三个加密历史备份和最新报表。采集可复用看板的 Python 虚拟环境。
2. 在采集 checkout 执行 `powershell -NoProfile -File .\scripts\run_local_collection.ps1 -Seed`，再以 `-Check` 核对三个广告账号和两份加密快照的覆盖日期。初始化数据位于 `%LOCALAPPDATA%\Baidu.Ads\storage`。
3. 用 `-Initialize -SiteConfig '<BAIDU_TONGJI_SSW_SITE_JSON>'` 保存采集配置。用于首次导入历史的解密密钥、百度统计授权 JSON 和 PostHog 查询密钥通过 Windows 密码框输入，保存在当前账号的 DPAPI 配置中。广告账号凭据从各自的加密历史恢复。PostHog 快照密钥及微信客服配置复用现有设置。
4. 手动执行一次脚本，然后用 `-Check` 和 `storage\collection.log` 验证。实时采集验证通过后，用 `-InstallTask` 注册后台采集任务。
5. 部署带本地读取功能的看板代码后，在 `storage` 内创建空的 `use-local` 文件并重启 `baidu-ads-dashboard-login`。登录看板核对 TinaCMS、YakShaver 和 SSW。确认 GitHub 没有正在运行的采集，再停用 GitHub 的 `collect.yml` 和 Windows 的旧 `baidu-ads-ssw-wecom` 任务。GitHub Pages 继续提供旧链接跳转。

## 发布与告警

采集器从当前已验证版本开始，分别更新三个账户的广告报表、百度统计各来源、PostHog 和微信客服。广告报表须核对本轮运行时间、各报表状态和截至北京昨天的覆盖日期。校验通过后，采集器一次切换 `storage\current`，同一个看板请求始终读取同一版本。

| 本轮结果 | 看板及任务状态 |
| --- | --- |
| 所有必需来源更新成功 | 发布新版本，任务返回 0。 |
| 个别来源采集失败或覆盖不足 | 发布通过校验的来源，失败来源沿用上次结果。看板列出失败来源和可确认的覆盖日期，任务返回非零。 |
| 整个版本未通过发布校验 | 当前版本继续提供服务。 |

### SSW 付费联系转化

看板优先使用百度统计「全部来源」报表（`source/all/a`）的百度付费搜索会话及其「联系我们点击」次数。「推广趋势」（`pro/hour/a`）是辅助报表。查询成功但访问次数返回 `--`，导致没有昨天的数值日报时，采集器在日志写入 `SSW promotion trends: no numeric visits for` 和日期，继续校验来源归因。来源归因覆盖昨天时，此情形不触发看板告警或非零退出。来源归因也未更新时，看板仍提示实际覆盖日期。鉴权失败、请求异常或响应格式错误仍走采集失败流程。

2026-11-27 在生产重跑，推广趋势缺少 11 月 26 日访问次数，来源归因已覆盖当天。发布版本 `2e438193` 的 SSW 告警列表为空，任务返回 0。

### 排查与回退

1. 在任务计划程序检查 `baidu-ads-collect-local` 的最近运行结果。打开 `%LOCALAPPDATA%\Baidu.Ads\storage\collection.log`，定位本轮的 `Validated local report revision`。部分成功时，末尾的 `Partial collection` 列出需排查的账户和来源。
2. 使用 `scripts\run_local_collection.ps1 -Check` 核对当前发布版本中三个广告报表和两份加密快照的覆盖日期。SSW 联系转化另查 SSW 看板的来源归因日期。排除故障后重新运行同一采集任务即可重试。
3. 广告采集锁被占用时，广告采集器返回 75。权限或文件系统错误单独报错。凭据和旧状态均缺少令牌到期日时，核实并补齐 `token_expires`。

需要回退到旧读取方式时，移走 `use-local` 并重启看板任务。恢复旧采集任务前先停 ECS 本地采集任务。新增数据和加密快照留在现有 ECS。整块云盘丢失后，迁移后数据的恢复取决于上游 API 的历史保留期。
