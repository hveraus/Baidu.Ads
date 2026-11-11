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

## SSW China website conversions

- The Baidu Tongji site **SSW 中国站** uses site ID `23684705` under 智鹏瑞尔. The **联系我们点击** goal uses ID `1557094` and counts visits to the virtual path `/__analytics/conversion/contact-click` on both `ssw.com.cn` and `www.ssw.com.cn`, once per session. The website source is `ZPREHZ/ssw.com.cn`. [Baidu Tongji goal settings](https://tongji.baidu.com/main/setting/90172887/home/trans/basicsetting?flag=page&siteId=23684705).
- The **Sync SSW website conversions** step reuses the Baidu Tongji read credentials from `BAIDU_TONGJI_CREDENTIALS_JSON`. The Actions repository variable `BAIDU_TONGJI_SSW_SITE_JSON` supplies `site_id`, `contact_click_target`, and `start_date` for SSW. The task runs at 09:00 Beijing time and queries through yesterday.
- The SSW overview divides sessions with a contact click by all sessions on the same covered dates. It reads aggregate counts from `accounts/ssw/data/website-conversions.json`. Check the SSW collection step and the website conversion card after a production test click has reached Baidu Tongji. [SSW overview](https://hveraus.github.io/Baidu.Ads/ssw/).
