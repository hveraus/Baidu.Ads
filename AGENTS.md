# Baidu Ads project instructions

- Read README.md and docs/DATA.md before changing data publication.
- This repository is public. GitHub Pages serves the public redirect entry. Never commit `.credentials.json`, `.backup-key`, visitor IP addresses, raw SQLite/CSV files, or `work/`.
- Preserve raw data through `scripts/vault.py`; use authenticated encryption and keep the key in repository Secrets or a private recovery file.
- Public data must be produced by `scripts/publish_data.py`; do not copy the private SQLite database into `data/`.
- Keep source keywords, search terms, campaign names and existing ad copy unchanged when translating UI text.
- Run `python -m unittest discover -s tests -v`, `python scripts/verify_data.py`, and `python scripts/check_public.py` before publishing data changes.
- Do not enable automatic bid, budget, keyword or campaign mutations. The collector intentionally has a read-only endpoint allowlist.
- The UI uses `dashboard.template.html`, `i18n.en.json` and `i18n.py`; regenerate both languages with `python dashboard.py --public` for Pages.
- All setup scripts must be relative to the checkout, not tied to one user's home directory.


## Tina CLI analytics from PostHog

- The collector queries TinaCloud project `237683` and counts `create-tina-app-started` and `create-tina-app-finished` events with `$geoip_country_code = CN` over the latest 90 complete dates in Asia/Shanghai. The geographic filter covers TinaCloud events regardless of referring site. Baidu Tongji website conversions appear in the Website conversion rate section. [Tina dashboard and country map](https://us.posthog.com/project/237683/dashboard/892475).
- The Collect Baidu Ads data workflow runs daily at 09:00 Beijing time and supports manual runs on `main`. The **Sync encrypted Tina CLI totals** step calls `scripts/sync_posthog_cli.py`. Its success message is `Encrypted PostHog snapshot saved.` A query error keeps the previous snapshot. [Collection workflow](https://github.com/hveraus/Baidu.Ads/actions/workflows/collect.yml).
- `POSTHOG_CLI_QUERY_KEY` in GitHub Actions Secrets has `Query: Read` access to the TinaCloud project. `POSTHOG_CLI_SNAPSHOT_KEY` is the independent 64-character hexadecimal AES-256-GCM key. Find the viewing key in the private Keeper record `Baidu.Ads PostHog 查看密钥`. Keep both key values and plaintext PostHog counts out of Git, public reports, workflow logs, and local exports.
- The repository stores the encrypted `data/posthog-cli.enc` snapshot. The China Tina CLI section opens automatically after dashboard sign-in. Both language versions use the selected date range. The server decrypts the snapshot after authentication. [TinaCMS overview](https://ads.ssw.com.cn/).
- To verify a run, check the collection step log, confirm `data/posthog-cli.enc` was updated on `main`, and check that the Pages deploy completed. Sign in to the overview and verify both event counts and the data coverage date. [Implementation PR](https://github.com/hveraus/Baidu.Ads/pull/2) · [First cloud run](https://github.com/hveraus/Baidu.Ads/actions/runs/42969827850).

## TinaCMS paid email enquiry clicks

- The TinaCMS overview counts Baidu paid sessions with the existing email enquiry click goal, once per session. The card follows the Beijing date range and combines all Baidu campaigns. A reported zero displays as 0, and missing goal data displays as —.
- **Sync TinaCMS paid email clicks** runs `scripts/sync_website_conversions.py --site tinacms-paid` with the existing Baidu Tongji secret. It queries `pro/hour/a` with the TinaCMS site's `email_click_target` and writes only daily aggregates to `data/website-paid-email-clicks.json`.

## TinaCMS and SSW website traffic

- The overview shows site-wide PV, UV, aggregate IP count, average visit duration, and total conversions in a separate row. Both collection steps query Baidu Tongji's all-source `trend/time/a` totals for the latest 7, 30, and 90 complete Beijing dates, from each site's configured collection start date.
- The TinaCMS and SSW snapshots are `data/website-traffic.json` and `accounts/ssw/data/website-traffic.json`. Single-day selections use daily report values. Other custom ranges use complete daily counts for PV and conversions when exact range totals are unavailable. Daily UV, IP counts, and average durations are never combined into a range total.

## SSW China website conversions

- The Baidu Tongji site **SSW 中国站** uses site ID `23684705` under 智鹏瑞尔. The **联系我们点击** goal uses ID `1557094` and counts visits to the virtual path `/__analytics/conversion/contact-click` on both `ssw.com.cn` and `www.ssw.com.cn`, once per session. The website source is `ZPREHZ/ssw.com.cn`. [Baidu Tongji goal settings](https://tongji.baidu.com/main/setting/90172887/home/trans/basicsetting?flag=page&siteId=23684705).
- The **Sync SSW website conversions** step reuses the Baidu Tongji read credentials from `BAIDU_TONGJI_CREDENTIALS_JSON`. The Actions repository variable `BAIDU_TONGJI_SSW_SITE_JSON` supplies `site_id`, `contact_click_target`, and `start_date` for SSW. The task runs at 09:00 Beijing time and queries through yesterday.
- The SSW overview card counts Baidu paid sessions with a contact click over the selected Beijing dates across all campaigns. A reported zero displays as 0, and missing goal data displays as —. The collector reads daily totals from Baidu Tongji's promotion trends report (`pro/hour/a`, all Baidu promotion types), and applies the SSW contact goal to the conversion count. The paid snapshot is `accounts/ssw/data/website-paid-conversions.json`. Compare it with the SSW promotion trends report for the same dates and goal. [SSW overview](https://ads.ssw.com.cn/ssw/).

## SSW WeChat Customer Service consultations

- In 智鹏瑞尔 WeCom, use the self-built app **SSW 客服统计** in **WeChat Customer Service → API → Apps that can call APIs**. The SSW website's Aliyun Windows server is the intended trusted-IP host. The collector uses the SSW website customer service account's `open_kfid`.
- The live site's `/media/wecom-qr.jpg` resolves to a WeChat Customer Service entry under `work.weixin.qq.com/kfid/`. On 2026-11-12, the Aliyun server authenticated, found **SSW China**, and reconciled October's consultation customer count with the WeCom dashboard. Some dates did not carry a `customer_cnt` field. Keep consultation counts in the encrypted snapshot.
- The `kf/get_corp_statistic` API supplies `customer_cnt` and `customer_msg_cnt` by Beijing date. The overview sums consulting customers and customer-sent messages separately across the selected dates and all sources for the SSW China account. Preserve explicit zeros and count coverage independently for each field. The encrypted snapshot contains aggregate counts only.
- The production checkout on the Aliyun Windows server is `%USERPROFILE%\baidu-ads-wecom`. Task Scheduler runs `baidu-ads-ssw-wecom` daily at 08:30 Beijing time. `scripts/run_wecom_consultations.ps1` uses the scheduled Windows account's DPAPI-protected configuration. Verify with `schtasks /query /tn baidu-ads-ssw-wecom /fo list /v` and look for a recent `Last Run Time` and `Last Result: 0`.
- The repository carries the authenticated encrypted snapshot at `accounts/ssw/data/wecom-consultations.enc`. The server uses the key recorded in Keeper as `Baidu.Ads - SSW 微信客服 - 查看密钥` to decrypt it after dashboard sign-in. The first on-demand run from Task Scheduler succeeded on 2026-11-12 and published through GitHub Pages. [Setup and verification](docs/WECOM_CONSULTATIONS.md) · [Verified Pages run](https://github.com/hveraus/Baidu.Ads/actions/runs/43193376526).

## Dashboard sign-in

- The service on the Aliyun Windows host gates all three bilingual dashboards at `https://ads.ssw.com.cn/`. One dashboard account shares a seven-day browser session across the three reports. After authentication, the service decrypts the Tina CLI and SSW WeCom aggregates for display.
- On 2026-11-12, the Aliyun checkout `%USERPROFILE%\baidu-ads-login` and its virtual environment were installed. The DNS A record was enabled and verified. Certify Certificate Manager issued a separate Let's Encrypt certificate. Automatic renewal, PEM export, and the Nginx reload task are configured and the reload task was tested.
- GitHub OAuth was retired after repeated TCP timeouts from the Aliyun host to the OAuth token endpoint. Sign-in uses a local account and a salted scrypt password hash. The service still reads reports from the GitHub repository. Git refresh failures use the last downloaded revision.
- Follow [deployment and cutover](docs/DASHBOARD_LOGIN.md) to configure the password and Windows startup task `baidu-ads-dashboard-login` under the WeCom collector user. The private configuration lives at `%LOCALAPPDATA%\Baidu.Ads\dashboard-login.xml`. Keep passwords, password hashes and snapshot keys out of Git and workflow logs.
- On 2026-11-12, the Windows startup task was installed and confirmed running. The saved Tina CLI key was replaced after validation against the snapshot. Safari verified all three reports and both private panels in one authenticated session. Anonymous requests showed the login form, and an incorrect password was rejected. Logout is covered by the test suite.
- `BAIDU_ADS_REQUIRE_LOGIN=true` makes GitHub Pages publish only redirects to the corresponding pages on `https://ads.ssw.com.cn/`. [Cutover deployment](https://github.com/hveraus/Baidu.Ads/actions/runs/43224682239) succeeded and all nine existing report URLs were verified. The Git repository still contains public advertising aggregates and encrypted private snapshots. Collection schedules remain independent of login.

## Aliyun collection and local report storage

The Windows cutover procedure and rollback are in [docs/ALIYUN_COLLECTION.md](docs/ALIYUN_COLLECTION.md). Keep the live GitHub and WeCom tasks enabled until the first ECS collection and authenticated dashboard have been verified. After cutover the active data comes from ECS storage. GitHub retains the code, old report history and Pages redirect.
