# Baidu.Ads · 百度投流分析

中文 / English 本地与在线分析工具。通过百度营销官方 API 收集 16 类报告，包含全部当前关键词及其表现、实际搜索词、广告文案、落地页、地域、设备、匹配、小时、转化和无效点击。

**在线页面：[hveraus.github.io/Baidu.Ads](https://hveraus.github.io/Baidu.Ads/)**

> 本仓库与 GitHub Pages 页面公开。投放统计、关键词、搜索词和广告内容可被访问者看到。百度令牌和访客 IP 不进入公开页面或明文数据；完整原始数据以加密备份保存。

## 换电脑怎么用

### 只查看 / 分享

直接打开在线页面，不需要百度账号或令牌。右上角切换中文 / English。日期、筛选、搜索、导出和关键词详情均可在浏览器中使用。行动勾选与搜索词复核仅保存在当前浏览器，不会修改广告，也不会同步给其他访问者。

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

1. **云端采集**：登录仓库，在 Actions → **Collect Baidu Ads data** → Run workflow。已配置的 Repository Secrets 会继续使用，无需把令牌复制到新电脑。
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

- `.github/workflows/collect.yml`：计划每天 **北京时间 09:00** 运行，GitHub 调度可能延迟；也可手动运行。
- 每次恢复加密历史 → 调用只读 API → 对账 → 导出去 IP 的公开数据 → 重新生成中英文页面 → 加密原始数据 → 提交更新 → 部署 Pages。
- 默认重拉最近 7 天以更新迟到转化；中断较久时根据成功覆盖范围补采。
- `.github/workflows/pages.yml`：主分支更新时部署，也可单独手动发布当前历史快照，不需要百度凭据。
- 云端运行不依赖这台电脑开机。失败可在 GitHub Actions 查看；令牌临近到期会出现在运行摘要，需主动续期。GitHub 通知取决于你的账号设置。
- 令牌有效期依据百度界面填写。当前登记日期为 **2027-01-25**，不把自授权令牌当作普通 24 小时 OAuth 令牌刷新。

## 凭据放在哪里

| 内容 | 存放位置 | 是否公开 |
|---|---|---|
| UI、采集方法、报告配置、文档、测试 | Git 仓库 | 是 |
| 全部公开统计历史、CSV、去 IP 的 SQLite | `data/` | 是 |
| 中英文页面 | `report/` 与 Pages | 是 |
| 完整原始数据、访客 IP、运行记录和凭据的加密包 | `backups/private-data.enc` | 只有密文公开 |
| 百度访问令牌 | `BAIDU_CREDENTIALS_JSON` Repository Secret；本机 `.credentials.json` | 否 |
| 原始数据恢复密钥 | `DATA_BACKUP_KEY` Repository Secret；本机 `.backup-key` 和单独交付的恢复文件 | 否 |

GitHub Secrets 无法在设置页读回明文。**请保管恢复密钥**，它没有提交到仓库。其他电脑可以直接使用在线页面或运行云端任务；本机恢复完整私有历史需要自己的恢复文件。不要把恢复文件、令牌放进 issue、代码或 Pages。

首次配置或续期后，可在已登录 `gh` 的本机运行：

```sh
python scripts/configure_github.py --repo hveraus/Baidu.Ads
```

该脚本通过 stdin 上传已有本机凭据与恢复密钥，不在命令参数或日志中输出值。详细步骤见 [GitHub 与恢复](docs/GITHUB.md)。

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

## 多网站独立账户

左上角标题下拉可在 TinaCMS、YakShaver 和 SSW 之间切换，每个账户拥有独立的关键词、消费、历史数据和建议。原始关键词与广告文案保留原文。YakShaver 的建议不沿用 TinaCMS 的文档管理意图判断或预算分组。

- TinaCMS 继续使用根目录原有数据及 `BAIDU_CREDENTIALS_JSON` Secret。
- YakShaver 账户 ID 为 `91119162`，推广账户名 `智鹏瑞尔-1`，使用 `BAIDU_YAKSHAVER_CREDENTIALS_JSON` Secret。
- YakShaver 原始数据与凭据仅在 `.private/yakshaver/`，公开数据在 `accounts/yakshaver/data/`，加密备份在 `accounts/yakshaver/backups/private-data.enc`。
- 加密备份复用原有 `DATA_BACKUP_KEY`，可使用已交付的私人恢复密钥。自授权令牌登记有效期到 **2027-02-01**。
- 应用登记的回调字段为 `https://yakshaver.ai`，与 TinaCMS 相同使用产品官网首页；当前没有启用 OAuth 回调，不将官网首页视为已实现的回调接口。实际推广报告中的落地页为 `http://yakshaver.com.cn/`。

```sh
python scripts/accounts.py build                       # 从公开历史生成两个账户的页面，无需令牌
python scripts/accounts.py restore --account yakshaver # 用恢复密钥恢复 YakShaver 私有历史
python scripts/accounts.py collect --account yakshaver # 增量采集、对账、公开导出和加密
python scripts/accounts.py collect --account yakshaver --backfill
python scripts/accounts.py configure-github --account yakshaver
```

新电脑仅查看时运行 `scripts/accounts.py build`；恢复完整数据时先按照上方说明设置 `DATA_BACKUP_KEY` 或将私有恢复密钥保存为根目录 `.backup-key`，再执行 `restore`。如需重新授权，可把新凭据安全保存到 `.private/yakshaver/.credentials.json`，字段格式参考 `.credentials.example.json`，并更新对应 GitHub Secret。云端每天依次采集 TinaCMS、YakShaver 和 SSW，全部对账通过后发布。任何账户失败时保留上一版线上报告，不发布部分更新；不会修改广告。

### SSW 独立账户

- 应用 `ssw` 已生效，推广账户 `智鹏瑞尔-2`（ID `91119163`）。回调字段登记官网 `https://ssw.cn`，采集使用自授权，不依赖 OAuth 回调。
- 令牌登记有效期 **2027-02-01**。GitHub 使用独立 Secret `BAIDU_SSW_CREDENTIALS_JSON`。
- 私有数据在 `.private/ssw/`，去 IP 的公开数据在 `accounts/ssw/data/`，完整加密备份在 `accounts/ssw/backups/private-data.enc`。
- 在线页面：[SSW 百度投流分析](https://hveraus.github.io/Baidu.Ads/ssw/)。下拉切换账户时保留语言和栏目。

恢复、回补和更新 Secret 的命令与 YakShaver 相同，把参数改为 `--account ssw` 即可。复用原有私人恢复密钥，各账户数据分别恢复与对账。
