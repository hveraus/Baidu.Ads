# GitHub Pages、Secrets 与新电脑恢复

## 仓库设置

仓库为 `hveraus/Baidu.Ads`，公开。Settings → Pages → Source 为 **GitHub Actions**。静态发布目录是 `report/`，只上传中英文页面与 `.nojekyll`，不上传原始数据文件、恢复密钥或加密包。加密包保存在 Git 仓库以便跨电脑恢复。

Actions Repository Secrets：

- `BAIDU_CREDENTIALS_JSON`：`.credentials.json` 的 JSON 内容。
- `DATA_BACKUP_KEY`：32 字节随机密钥的 64 位十六进制文本。

值不可写入 workflow YAML。`configure_github.py` 使用 stdin 调用 `gh secret set`，避免出现在命令参数和日志中。GitHub 的 Secrets 页面不能读回原值。

## 恢复矩阵

| 需求 | 需要什么 |
|---|---|
| 给别人查看 | Pages 链接，无需登录 |
| 新电脑查看全部公开统计 | clone 仓库即可 |
| 在新电脑触发云端更新 | 仓库维护权限；Actions 沿用已保存 Secrets |
| 新电脑恢复原始 IP 与凭据 | clone + 私人保存的恢复密钥 |
| 恢复密钥丢失后继续采集 | 从百度重新获取授权；公开历史仍可用，但旧加密私有明细无法靠公开仓库解密 |

不要用打印 Actions Secret 的方式取回密钥。你单独保存的恢复文件是本机完整恢复入口；不要把它加到仓库或发给报表访客。

## 日常操作

- 立即更新：Actions → Collect Baidu Ads data → Run workflow。
- 只部署已有数据：Actions → Publish GitHub Pages → Run workflow。
- 查看错误：进入失败运行的日志；脚本不会主动打印令牌。
- 授权续期：百度后台续期 → 本机更新 `.credentials.json` → `configure_github.py` → 手动运行一次采集检查。
- 如果恢复密钥轮换，先用旧密钥解密，再用新密钥生成备份并更新 Secret；不要直接覆盖密钥导致历史不可解密。

工作流只在受信任的主分支上使用 Secrets，不为 pull_request 或 fork 提供百度令牌。并发采集被串行化，写入仓库只使用该仓库的 `GITHUB_TOKEN`。页面部署依赖公开快照，不需要读取百度 Secrets。

## 原有本机定时任务

云端定时采集负责发布版本。若保留本机采集，新增本机数据不会自动进入线上页面，必须验证、导出公开层、加密备份并提交到 GitHub；避免多台电脑同时推送不同快照。推荐云端作为日常采集来源，本机用于分析和人工恢复。

## 官方文档

- [GitHub Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [GitHub Actions Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
