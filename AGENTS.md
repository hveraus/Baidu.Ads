# Baidu Ads project instructions

- Read README.md and docs/DATA.md before changing data publication.
- This repository and GitHub Pages site are public. Never commit `.credentials.json`, `.backup-key`, visitor IP addresses, raw SQLite/CSV files, or `work/`.
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
- The repository stores the encrypted `data/posthog-cli.enc` snapshot. Open the TinaCMS overview, find the China Tina CLI section, and unlock it with the Keeper viewing key. Both language versions use the selected dashboard date range. The page decrypts the snapshot in the browser and asks for the key again after a reload. [TinaCMS overview](https://hveraus.github.io/Baidu.Ads/).
- To verify a run, check the collection step log, confirm `data/posthog-cli.enc` was updated on `main`, and check that the Pages deploy completed. Open the overview and verify both event counts and the data coverage date after unlocking. [Implementation PR](https://github.com/hveraus/Baidu.Ads/pull/2) · [First cloud run](https://github.com/hveraus/Baidu.Ads/actions/runs/42969827850).

## TinaCMS paid email enquiry clicks

- The TinaCMS overview counts Baidu paid sessions with the existing email enquiry click goal, once per session. The card follows the Beijing date range and combines all Baidu campaigns. A reported zero displays as 0, and missing goal data displays as —.
- **Sync TinaCMS paid email clicks** runs `scripts/sync_website_conversions.py --site tinacms-paid` with the existing Baidu Tongji secret. It queries `pro/hour/a` with the TinaCMS site's `email_click_target` and writes only daily aggregates to `data/website-paid-email-clicks.json`.

## TinaCMS and SSW website traffic

- The overview shows site-wide PV, UV, aggregate IP count, average visit duration, and total conversions in a separate row. Both collection steps query Baidu Tongji's all-source `trend/time/a` totals for the latest 7, 30, and 90 complete Beijing dates, from each site's configured collection start date.
- The TinaCMS and SSW snapshots are `data/website-traffic.json` and `accounts/ssw/data/website-traffic.json`. Single-day selections use daily report values. Other custom ranges use complete daily counts for PV and conversions when exact range totals are unavailable. Daily UV, IP counts, and average durations are never combined into a range total.

## SSW China website conversions

- The Baidu Tongji site **SSW 中国站** uses site ID `23684705` under 智鹏瑞尔. The **联系我们点击** goal uses ID `1557094` and counts visits to the virtual path `/__analytics/conversion/contact-click` on both `ssw.com.cn` and `www.ssw.com.cn`, once per session. The website source is `ZPREHZ/ssw.com.cn`. [Baidu Tongji goal settings](https://tongji.baidu.com/main/setting/90172887/home/trans/basicsetting?flag=page&siteId=23684705).
- The **Sync SSW website conversions** step reuses the Baidu Tongji read credentials from `BAIDU_TONGJI_CREDENTIALS_JSON`. The Actions repository variable `BAIDU_TONGJI_SSW_SITE_JSON` supplies `site_id`, `contact_click_target`, and `start_date` for SSW. The task runs at 09:00 Beijing time and queries through yesterday.
- The SSW overview card counts Baidu paid sessions with a contact click over the selected Beijing dates across all campaigns. A reported zero displays as 0, and missing goal data displays as —. The collector reads daily totals from Baidu Tongji's promotion trends report (`pro/hour/a`, all Baidu promotion types), and applies the SSW contact goal to the conversion count. The paid snapshot is `accounts/ssw/data/website-paid-conversions.json`. Compare it with the SSW promotion trends report for the same dates and goal. [SSW overview](https://hveraus.github.io/Baidu.Ads/ssw/).

## SSW WeChat Customer Service consultations

- In 智鹏瑞尔 WeCom, use the self-built app **SSW 客服统计** in **WeChat Customer Service → API → Apps that can call APIs**. The SSW website's Aliyun Windows server is the intended trusted-IP host. The collector uses the SSW website customer service account's `open_kfid`.
- The live site's `/media/wecom-qr.jpg` resolves to a WeChat Customer Service entry under `work.weixin.qq.com/kfid/`. On 2026-11-12, the Aliyun server authenticated, found **SSW China**, and reconciled October's consultation customer count with the WeCom dashboard. Some dates did not carry a `customer_cnt` field. Keep consultation counts in the encrypted snapshot.
- The `kf/get_corp_statistic` API supplies `customer_cnt` by Beijing date. The overview sums the daily counts across the selected dates, including all sources for that customer service account. Preserve explicit zeros and show date coverage. This metric is separate from the Baidu Tongji paid contact click goal.
- `scripts/run_wecom_consultations.ps1` configures the collector under the scheduled task's Windows account, protects secrets with Windows DPAPI, and runs the Python collector. The task should run after 06:00 Beijing time. The repository and SSW overview carry only the authenticated encrypted snapshot at `accounts/ssw/data/wecom-consultations.enc`. Store the independent viewing key in the owner's password manager. [Setup and verification](docs/WECOM_CONSULTATIONS.md).
