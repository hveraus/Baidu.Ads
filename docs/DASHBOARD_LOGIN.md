# Baidu.Ads 看板登录

登录入口为 `https://ads.ssw.com.cn/`。HTTPS 已启用，登录服务等待配置 GitHub OAuth。完成下列上线核对前，原 Pages 继续提供静态报表。上线后，TinaCMS、YakShaver、SSW 的中英文页面共用一个 GitHub 登录会话，允许 GitHub 用户 `hveraus`（用户 ID `4745861`）查看。会话有效期 7 天，可以从页头退出。

服务端在登录通过后通过 Git 定期拉取主分支，读取广告报表与加密快照，在内存中解密中国区 Tina CLI 和 SSW 微信客服汇总。采集任务继续沿用现有的 GitHub Actions 和阿里云 Windows 任务。GitHub 仓库及历史中的广告汇总仍公开，PostHog 和微信客服快照仍以密文保存。

## GitHub 和域名

1. 由 `hveraus` 在 [GitHub 注册 OAuth App](https://github.com/settings/applications/new)。应用名称填 `Baidu.Ads Dashboard`，Homepage URL 填 `https://ads.ssw.com.cn/`，Authorization callback URL 填 `https://ads.ssw.com.cn/callback`。创建 Client Secret 后存入 Keeper。采集任务的 PAT 和这个登录应用的凭据各自独立。
2. 将 `ads.ssw.com.cn` 的 A 记录指向 SSW 官网的阿里云 Windows 主机 `106.14.246.176`，为该子域签发 HTTPS 证书。使用单独的 Nginx vhost 和单独的 HTTPS 证书配置，浏览器登录会话只发给该子域。

## Windows 部署

在运行微信客服采集的同一个 Windows 账号下执行。下面将登录服务放入独立 checkout。

```powershell
Set-Location $env:USERPROFILE
git clone https://github.com/hveraus/Baidu.Ads.git baidu-ads-login
Set-Location baidu-ads-login
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe login_dashboard.py --check-upstream
.\scripts\run_dashboard_login.ps1 -Initialize
```

Git 刷新按三分钟窗口检查，连接中断时读取最近一次成功拉取的提交。

初始化依次输入 OAuth Client ID、OAuth Client Secret、Keeper 中的 `Baidu.Ads PostHog 查看密钥`。脚本复用已由微信客服采集任务存入 DPAPI 的查看密钥，生成独立会话签名密钥并保存为同一用户下的 `%LOCALAPPDATA%\Baidu.Ads\dashboard-login.xml`。在设置或轮换时输入密钥，保留文件的默认用户权限。

将 `powershell.exe -NoProfile -File "<checkout>\scripts\run_dashboard_login.ps1"` 注册为 Windows 任务 `baidu-ads-dashboard-login`，使用同一个 Windows 用户，开机时启动，选择无论用户是否登录都运行，取消运行时长限制并启用失败重试。立即启动任务，以 `Host: ads.ssw.com.cn` 请求 `http://127.0.0.1:8765/health` 确认服务返回 `OK`。服务只监听回环地址。

## HTTPS

阿里云服务器的 Nginx 安装目录为 `C:\SSW\Nginx`。以管理员身份运行 `.\scripts\deploy_dashboard_proxy.ps1 -ReloadAsSystem` 启用子域独立的 ACME HTTP 验证入口。在 Certify Certificate Manager 为 `ads.ssw.com.cn` 新建证书，使用 HTTP-01 和站点根目录 `C:\SSW\Nginx\acme`。将完整证书链与私钥导出到 `C:\SSW\Certificate\BaiduAds\fullchain.pem` 和 `C:\SSW\Certificate\BaiduAds\private.pem`，并在续期导出后重载 Nginx。将证书目录及私钥的访问权限限制在 SYSTEM 和 Administrators。Certify 的重载任务运行 `C:/SSW/Nginx/nginx.exe`，参数为 `-p C:/SSW/Nginx/ -s reload`。任务放在 PEM 导出之后，仅在成功时运行。

签发证书后运行 `.\scripts\deploy_dashboard_proxy.ps1 -EnableHttps -ReloadAsSystem` 启用 HTTPS 反向代理。脚本先运行 `nginx -t`，通过后由一次性 Windows 任务使用现有 Nginx 服务的 SYSTEM 身份重载。HTTPS 服务代理至 `127.0.0.1:8765`，等待登录服务启动时返回 503。

## 上线核对

1. 以未登录浏览器打开三个账户页面，确认只显示 GitHub 登录入口，响应无报表数据。以已授权的账号登录一次，检查三个账户可切换、Tina CLI 与微信客服数据可直接查看，退出后回到登录页。用另一个 GitHub 账号核对访问会被拒绝。
2. 新入口验证成功后设置仓库 Actions 变量 `BAIDU_ADS_REQUIRE_LOGIN=true`，运行 `Publish GitHub Pages`。等待部署结束，检查所有旧地址进入相应的登录保护页面。Pages 发布包此时只包含跳转入口。
3. 核对现有采集仍成功更新主分支，以及旧入口与新域名都能在新浏览器会话中到达登录页。记录部署状态到 `AGENTS.md`。
