# Baidu.Ads 看板登录

登录入口为 `https://ads.ssw.com.cn/`。TinaCMS、YakShaver、SSW 的中英文页面共用一个看板账号和七天浏览器会话，可从页头退出。HTTPS 已部署。在新登录验证完成前，原 Pages 继续提供静态报表。

登录后，服务端通过 Git 定期读取主分支的广告报表，在内存中解密中国区 Tina CLI 和 SSW 微信客服汇总。现有 GitHub Actions 和阿里云 Windows 采集任务继续运行。GitHub 仓库及历史中的广告汇总仍公开，PostHog 和微信客服快照仍以密文保存。

## 域名

`ads.ssw.com.cn` 的 A 记录指向 SSW 官网的阿里云 Windows 主机 `106.14.246.176`。看板使用独立的 Nginx vhost 和 HTTPS 证书，浏览器会话只发给该子域。

## Windows 部署

在运行微信客服采集的同一个 Windows 账号下执行。下面将登录服务放入独立 checkout。

```powershell
Set-Location $env:USERPROFILE
git clone https://github.com/hveraus/Baidu.Ads.git baidu-ads-login
Set-Location baidu-ads-login
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe login_dashboard.py --check-upstream
.\scripts\run_dashboard_login.ps1 -Initialize -InstallTask
```

Git 刷新按三分钟窗口检查，连接中断时读取最近一次成功拉取的提交。

初始化时输入看板账号，留空使用 `ken`，再在两个密码框中输入并确认至少 15 个字符的密码。可在 Keeper 中保存为「Baidu.Ads 看板登录」。初始化复用已保存的 Tina CLI 查看密钥。首次部署时输入原有查看密钥。脚本复用微信客服采集任务存入 DPAPI 的查看密钥，保存加盐的 scrypt 密码哈希和新的会话签名密钥到同一用户下的 `%LOCALAPPDATA%\Baidu.Ads\dashboard-login.xml`。每次初始化会结束之前的看板会话。失败尝试达到 5 次后暂停登录最多一分钟。

最后一个密码框输入当前 Windows 账户的密码。脚本以该账号注册并启动任务 `baidu-ads-dashboard-login`，开机自动运行。可分别用 `-Initialize` 更新看板密码，或用 `-InstallTask` 注册并启动任务。更新密码后重启任务。以 `Host: ads.ssw.com.cn` 请求 `http://127.0.0.1:8765/health`，确认返回 `OK`。服务只监听回环地址。

如果 Tina CLI 密钥与快照不匹配，运行 `.\scripts\run_dashboard_login.ps1 -UpdatePostHogKey`。在密码框输入 Keeper 中「Baidu.Ads PostHog 查看密钥」记录的 64 位十六进制密钥。脚本先验证快照能解密，再保存密钥并重启任务。看板账号、密码和会话签名密钥继续使用当前值。

## HTTPS

阿里云服务器的 Nginx 安装目录为 `C:\SSW\Nginx`。以管理员身份运行 `.\scripts\deploy_dashboard_proxy.ps1 -ReloadAsSystem` 启用子域独立的 ACME HTTP 验证入口。在 Certify Certificate Manager 为 `ads.ssw.com.cn` 新建证书，使用 HTTP-01 和站点根目录 `C:\SSW\Nginx\acme`。将完整证书链与私钥导出到 `C:\SSW\Certificate\BaiduAds\fullchain.pem` 和 `C:\SSW\Certificate\BaiduAds\private.pem`，并在续期导出后重载 Nginx。将证书目录及私钥的访问权限限制在 SYSTEM 和 Administrators。Certify 的重载任务运行 `C:/SSW/Nginx/nginx.exe`，参数为 `-p C:/SSW/Nginx/ -s reload`。任务放在 PEM 导出之后，仅在成功时运行。

签发证书后运行 `.\scripts\deploy_dashboard_proxy.ps1 -EnableHttps -ReloadAsSystem` 启用 HTTPS 反向代理。脚本先运行 `nginx -t`，通过后由一次性 Windows 任务使用现有 Nginx 服务的 SYSTEM 身份重载。HTTPS 服务代理至 `127.0.0.1:8765`，等待登录服务启动时返回 503。

## 上线核对

1. 以未登录浏览器打开三个账户页面，确认只显示账号密码表单，响应无报表数据。用配置的账号登录一次，检查三个账户可切换、Tina CLI 与微信客服数据可直接查看，退出后回到登录页。用错误的密码核对访问会被拒绝。
2. 新入口验证成功后设置仓库 Actions 变量 `BAIDU_ADS_REQUIRE_LOGIN=true`，运行 `Publish GitHub Pages`。等待部署结束，检查所有旧地址进入相应的登录保护页面。Pages 发布包此时只包含跳转入口。
3. 核对现有采集仍成功更新主分支，以及旧入口与新域名都能在新浏览器会话中到达登录页。记录部署状态到 `AGENTS.md`。
