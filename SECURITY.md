# Sensitive data handling

This is a public analytics repository, not an access-controlled dashboard. Only the intended advertising statistics and source advertising text may appear in `data/` and Pages.

Never commit access tokens, backup keys, `.credentials.json`, raw visitor IPs or unencrypted private databases. The complete private history is stored in `backups/private-data.enc`, authenticated with AES-256-GCM. Keep the recovery key separately.

If a token is accidentally exposed, revoke or reset it in Baidu and update the repository secret and encrypted backup. Removing a file in a later commit does not remove it from Git history.
