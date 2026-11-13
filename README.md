# Baidu.Ads · 百度投流分析

中文 / English 本地与在线分析工具。通过百度营销官方 API 收集 16 类报告，包含全部当前关键词及其表现、实际搜索词、广告文案、落地页、地域、设备、匹配、小时、转化和无效点击。

**在线页面：[ads.ssw.com.cn](https://ads.ssw.com.cn/)**

> 在线页面需要看板账号登录。新采集的数据保存在阿里云 ECS。GitHub 仓库公开，仍可查看迁移前的广告统计、关键词、搜索词和广告文案。

## 换电脑怎么用

### 只查看 / 分享

用看板账号密码登录，TinaCMS、YakShaver 和 SSW 共用一个登录会话。旧的 GitHub Pages 书签会自动跳转。[整站登录部署说明](docs/DASHBOARD_LOGIN.md)。右上角切换中文 / English。日期、筛选、搜索、导出和关键词详情均可在浏览器中使用。行动勾选与搜索词复核仅保存在当前浏览器，不会修改广告，也不会同步给其他访问者。

### 获取代码和历史数据

```sh
git clone https://github.com/hveraus/Baidu.Ads.git
cd Baidu.Ads
python3 -m venv .venv
# macOS / Linux
source .venv/bin/activate
python -m pip install --only-binary=:all: -r requirements.txt
python dashboard.py --public
python -m http.server 8796 --bind 127.0.0.1 --directory report
```

打开 `http://127.0.0.1:8796/`。此方式从仓库中的公开 SQLite 历史数据生成页面，**不需要任何百度令牌**。也可以直接打开 `report/index.html` 离线查看。

Windows PowerShell：先执行 `py -3 -m venv .venv`，然后用 `.venv\Scripts\python.exe` 代替上述 `python`。建议 Python 3.12 或更新版本；Windows 环境的时区由 `tzdata` 提供。macOS 可双击 `查看分析页面.command`。

### 继续在新电脑采集

选一种方式：

1. **生产采集**：由阿里云 ECS 每日运行。手动更新时，在服务器上启动 `baidu-ads-collect-local` 任务。参见[阿里云采集与存储](docs/ALIYUN_COLLECTION.md)。
2. **本机恢复完整数据**：把自己的恢复密钥通过密码管理器等私人渠道带到新电脑，运行：

```sh
python scripts/vault.py restore --key-file /PRIVATE/PATH/Baidu.Ads-recovery-key.txt
python tracker.py
```

恢复过程会还原原始 SQLite、全部原始 CSV、历史运行记录和本机凭据。它默认拒绝覆盖内容不同的现有文件；如需覆盖，先备份，再明确使用 `--force`。

3. **重新取得百度授权**：无需恢复密钥也能保留公开统计历史。先执行以下命令，然后按 [接入说明](docs/BAIDU_API.md) 从自己的百度后台获取自授权令牌：

```sh
python scripts/publish_data.py --hydrate
python scripts/configure.py
python tracker.py
```

这种方式不会恢复历史访客 IP 明细；完整明细需要加密备份与恢复密钥。

## 云端更新与发布

- `baidu-ads-collect-local` 每天 **北京时间 09:00** 在阿里云 ECS 查询百度营销、百度统计、PostHog 和微信客服。
- 所有账户校验通过后，采集任务一次性更新看板使用的报表。失败时继续显示上一次成功报表。运行状态可在 Windows 任务计划程序及 `%LOCALAPPDATA%\Baidu.Ads\storage\collection.log` 查看。
- GitHub 保留代码、迁移前历史和 Pages 跳转入口。
- 令牌有效期依据百度界面填写。当前登记日期为 **2027-01-25**，不把自授权令牌当作普通 24 小时 OAuth 令牌刷新。

## 凭据放在哪里

| 内容 | 存放位置 | 是否公开 |
|---|---|---|
| UI、采集方法、报告配置、文档、测试 | Git 仓库 | 是 |
| 活跃报表和加密统计快照 | ECS 的 `%LOCALAPPDATA%\Baidu.Ads\storage` | 需登录查看报表 |
| 原始 SQLite、CSV、百度营销凭据 | ECS 的私有采集目录 | 否 |
| 百度统计、PostHog 和微信客服凭据 | ECS 的 DPAPI 配置文件 | 否 |
| 迁移前广告统计和加密历史 | GitHub 仓库中的 `data/`、`report/`、`backups/` 及 `accounts/` | 统计汇总及密文公开 |

百度授权续期后更新阿里云采集配置。恢复迁移前的加密历史时，使用原有恢复密钥。详细路径见[阿里云采集与存储](docs/ALIYUN_COLLECTION.md)。

## 常用命令

```sh
python tracker.py                                      # 增量采集
python tracker.py --start 2026-10-01 --end 2026-10-28   # 指定日期
python tracker.py --backfill                           # 按配置回补
python tracker.py --export-only                        # 只导出本机已有数据
python scripts/verify_data.py                         # 多报告及计费对账
python scripts/publish_data.py                        # 生成公开历史数据
python dashboard.py --public                          # 生成 Pages 页面
python scripts/vault.py backup                        # 生成完整加密备份
python scripts/vault.py verify                        # 验证加密备份
python scripts/check_public.py                        # 检查公开内容
python -m unittest discover -s tests -v
```

`--export-only` 不需要令牌，不调用百度。原始 API CSV 表头为中文；页面上的导出跟随当前界面语言。金额始终为人民币，现有关键词、搜索词与广告原文不翻译。

## 页面与分析边界

- 7 个栏目：总览、关键词、搜索词、广告与落地页、流量分布、投流建议 / 下一期、数据与口径。
- “无报告”与数值 0 分开显示；当前出价、质量度、暂停状态是最新快照，不能当作历史配置。
- 不同报告是同一消费的不同切面，不应相加。无效金额单独列示，不计为额外损失或已获退款。
- API 转化为 0 不能证明没有真实询盘，也不能直接确认漏报。TinaCMS 总览另有[网站转化率](docs/TINACMS_CONVERSIONS.md)，可从百度统计同步或导入会话汇总；未采集日期显示 `—`。
- 下一期预算、否定词、分组和文案都是建议草案；没有自动修改广告。

更多：[百度接入](docs/BAIDU_API.md) · [数据结构与口径](docs/DATA.md) · [GitHub 与恢复](docs/GITHUB.md)

### 中国区 Tina CLI 数据

采集任务按北京时间查询最近 90 个完整日期的中国区启动和完成次数。ECS 使用 TinaCloud 的 `Query: Read` 个人密钥采集，并用独立的快照密钥加密统计。登录看板后，TinaCMS 总览自动显示这些数字。

## 多网站独立账户

左上角标题下拉可在 TinaCMS、YakShaver 和 SSW 之间切换，每个账户拥有独立的关键词、消费、历史数据和建议。原始关键词与广告文案保留原文。YakShaver 的建议不沿用 TinaCMS 的文档管理意图判断或预算分组。

- TinaCMS 在 ECS 采集目录根目录保存原始数据和凭据。
- YakShaver 账户 ID 为 `91119162`，推广账户名 `智鹏瑞尔-1`。
- YakShaver 原始数据与凭据仅在 `.private/yakshaver/`，公开数据在 `accounts/yakshaver/data/`，加密备份在 `accounts/yakshaver/backups/private-data.enc`。
- 加密备份复用原有 `DATA_BACKUP_KEY`，可使用已交付的私人恢复密钥。自授权令牌登记有效期到 **2027-02-01**。
- 应用登记的回调字段为 `https://yakshaver.ai`，与 TinaCMS 相同使用产品官网首页；当前没有启用 OAuth 回调，不将官网首页视为已实现的回调接口。实际推广报告中的落地页为 `http://yakshaver.com.cn/`。

```sh
python scripts/accounts.py build                       # 从公开历史生成两个账户的页面，无需令牌
python scripts/accounts.py restore --account yakshaver # 用恢复密钥恢复 YakShaver 私有历史
python scripts/accounts.py collect --account yakshaver # 增量采集、对账、公开导出和加密
python scripts/accounts.py collect --account yakshaver --backfill
```

新电脑可以用 `scripts/accounts.py build` 生成历史报表。恢复迁移前的私有数据时，使用原有 `DATA_BACKUP_KEY` 再执行 `restore`。生产授权续期时，在 ECS 更新 `.private/yakshaver/.credentials.json`，字段格式参考 `.credentials.example.json`。

### SSW 独立账户

- 应用 `ssw` 已生效，推广账户 `智鹏瑞尔-2`（ID `91119163`）。回调字段登记官网 `https://ssw.cn`，采集使用自授权，不依赖 OAuth 回调。
- 令牌登记有效期 **2027-02-01**。生产授权在 ECS 的 `.private/ssw/.credentials.json` 更新。
- 私有数据在 `.private/ssw/`，去 IP 的公开数据在 `accounts/ssw/data/`，完整加密备份在 `accounts/ssw/backups/private-data.enc`。
- 在线页面：[SSW 百度投流分析](https://hveraus.github.io/Baidu.Ads/ssw/)。下拉切换账户时保留语言和栏目。

恢复历史和回补的命令与 YakShaver 相同，把参数改为 `--account ssw` 即可。
