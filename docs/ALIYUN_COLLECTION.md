# 阿里云本地采集和存储

在 ECS 上以现有看板和微信客服计划任务的 Windows 账号操作。看板使用独立的采集 checkout，避免生产数据被代码升级覆盖。Windows 使用中国标准时间，计划任务每天 09:00 运行。

1. 从 GitHub 克隆已验证的迁移版本到 `%USERPROFILE%\baidu-ads-collector`。首次迁移用同一版本带来的三个加密历史备份和最新报表。采集可复用看板的 Python 虚拟环境。
2. 在采集 checkout 执行 `powershell -NoProfile -File .\scripts\run_local_collection.ps1 -Seed`，再以 `-Check` 核对三个广告账号和两份加密快照的覆盖日期。初始化数据位于 `%LOCALAPPDATA%\Baidu.Ads\storage`。
3. 用 `-Initialize -SiteConfig '<BAIDU_TONGJI_SSW_SITE_JSON>' -OssUri 'oss://<private-bucket>/<prefix>' -Ossutil '<ossutil.exe 的完整路径>'` 保存采集配置。备份密钥、百度统计授权 JSON 和 PostHog 查询密钥通过 Windows 密码框输入，保存在当前账号的 DPAPI 配置中。广告账号凭据从各自的加密历史恢复。PostHog 快照密钥及微信客服配置复用现有设置。OSS 使用仅限备份路径的访问身份。若已单独配置 ossutil，可省略 `-Ossutil`。
4. 手动执行一次脚本，然后用 `-Check` 和 `storage\collection.log` 验证。确认 OSS 可取回本次加密备份后，用 `-InstallTask` 注册后台采集任务。
5. 部署带本地读取功能的看板代码后，在 `storage` 内创建空的 `use-local` 文件并重启 `baidu-ads-dashboard-login`。登录看板核对 TinaCMS、YakShaver 和 SSW。确认 GitHub 没有正在运行的采集，再停用 GitHub 的 `collect.yml` 和 Windows 的旧 `baidu-ads-ssw-wecom` 任务。GitHub Pages 继续提供旧链接跳转。

采集在私有 checkout 生成报表，校验完毕后一次切换 `storage\current`。每次看板请求固定读取同一批次的文件。失败时继续展示上一次成功报表。首次切换后的回退方式为移走 `use-local` 并重启看板任务，恢复旧采集任务前先停 ECS 本地采集任务。

OSS 对象包含报表、公开统计层、加密快照和三个历史备份。整个压缩包再使用现有备份密钥进行 AES-256-GCM 加密，格式为 `BAIDU-ADS-ECS-BACKUP-v1\n`、12 字节 nonce、密文及认证标签，头部用作关联数据。备份密钥和两份快照密钥需要单独保存。
