"""Public colleague onboarding. Contains no account data or credentials."""
import base64
import hashlib
from starlette.responses import HTMLResponse

STYLE = '''
:root{color-scheme:light dark;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.75;background:#f5f6f8;color:#20242b}*{box-sizing:border-box}body{margin:0}main{max-width:1040px;margin:auto;padding:36px 24px 64px}a{color:#2459ae;text-underline-offset:4px}nav{display:flex;gap:20px;flex-wrap:wrap;font-size:14px}header{padding:42px 0 24px}h1{font-size:clamp(30px,5vw,46px);line-height:1.25;margin:10px 0}h2{font-size:25px;margin-top:0}h3{font-size:18px}section{background:#fff;border:1px solid #dce0e6;border-radius:16px;padding:28px;margin:20px 0;scroll-margin-top:20px}.muted{color:#596574}.tag{font-size:13px;font-weight:650;letter-spacing:.06em}pre,blockquote{background:#f3f5f8;border:1px solid #dce0e6;border-radius:8px;padding:16px;margin:14px 0;overflow-wrap:anywhere;white-space:pre-wrap}code{font-size:14px}blockquote{border-left:3px solid #587bb0}li{margin:8px 0}details{border-top:1px solid #dce0e6;padding:16px 0}summary{cursor:pointer;font-weight:650}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:12px 8px;border-bottom:1px solid #dce0e6;vertical-align:top}.notice{border-left:3px solid #8b6a21;padding-left:16px}footer{font-size:13px;color:#596574}@media(max-width:600px){main{padding:20px 14px}section{padding:20px}td,th{padding:10px 4px}}@media(prefers-color-scheme:dark){:root{background:#14171c;color:#e9edf2}section{background:#1c2027;border-color:#363e49}pre,blockquote{background:#14171c;border-color:#363e49}a{color:#9cbfff}.muted,footer{color:#adb7c5}td,th,details{border-color:#363e49}}
'''
BODY = '''
<nav aria-label="主导航"><strong>Baidu Ads · MCP</strong><a href="https://ads.ssw.com.cn/">数据看板</a><a href="/login">管理登录</a><a href="/activity">操作历史</a></nav>
<header><span class="tag">同事使用指南</span><h1>用对话查询广告与网站数据</h1><p class="muted">把 Baidu Ads 连接到你常用的 AI 客户端，直接问花费、搜索词和转化情况。先连接，再登录，最后照着示例提问。</p></header>
<nav aria-label="页内导航"><a href="#connect">连接设置</a><a href="#clients">客户端配置</a><a href="#examples">提问实例</a><a href="#scope">数据与权限</a><a href="#help">常见问题</a></nav>
<section id="connect"><h2>1. 准备账号与连接地址</h2><p>使用你自己的<strong>广告看板账号和密码</strong>。没有账号或尚未开通 MCP 权限时，请联系管理员 Ken。无需申请百度 API Key，也不用填写 DeepSeek 或其他数据源密钥。</p><p>在客户端的远程 MCP 地址一栏粘贴：</p><pre><code>https://mcp.ads.ssw.com.cn/mcp</code></pre><p>连接类型：<strong>Streamable HTTP</strong>（部分客户端显示 HTTP）；登录方式：<strong>OAuth 浏览器授权</strong>。这是工具连接地址，直接用浏览器打开出现“未授权”是正常的。</p><ol><li>在客户端添加远程 MCP 服务，名称可填 <strong>Baidu Ads</strong>。</li><li>发起连接或点击登录。浏览器将打开 MCP 登录与授权页。</li><li>确认域名是 <strong>mcp.ads.ssw.com.cn</strong>，核对客户端和申请的权限，使用自己的看板账号登录并授权。</li><li>返回客户端，确认服务已连接、可以看到 <code>baidu_ads_</code> 开头的工具，然后发送下面的第一条示例。</li></ol><p class="notice">请从客户端发起授权；单独登录本网站不会自动连接客户端。普通同事使用查询权限即可。不要把密码或授权令牌贴进聊天。</p></section>
<section id="clients"><h2>2. 选择你的客户端</h2><p class="muted">服务使用标准远程 MCP + OAuth。不同客户端、版本和组织策略可能限制自定义连接；以下配置参考官方文档，不代表所有客户端已完成生产登录验收。</p>
<details open><summary>Cursor</summary><p>打开 MCP 设置，添加远程服务；也可在个人目录的 <code>~/.cursor/mcp.json</code> 中加入以下配置。如果文件已有其他服务，只合并 <code>mcpServers</code> 中的新条目，不要覆盖原配置。</p><pre><code>{
  "mcpServers": {
    "baidu-ads": {
      "url": "https://mcp.ads.ssw.com.cn/mcp"
    }
  }
}</code></pre><p>保存并重新打开 Cursor，进入 MCP 设置按提示完成登录。在 Agent 对话中启用该服务的查询工具。</p><a href="https://prod.cursor.com/help/customization/mcp">Cursor 官方配置说明</a></details>
<details><summary>Claude Code</summary><p>在终端执行以下命令，为当前用户添加服务：</p><pre><code>claude mcp add --transport http --scope user baidu-ads https://mcp.ads.ssw.com.cn/mcp</code></pre><p>打开 Claude Code，输入 <code>/mcp</code>，选择 baidu-ads 并按提示认证。完成浏览器授权后，回到对话中提问。</p><a href="https://code.claude.com/docs/en/mcp">Claude Code 官方配置说明</a></details>
<details><summary>其他支持远程 MCP 的客户端</summary><p>在应用的连接器、工具或 MCP 设置中选择“添加自定义远程服务”，填写上面的地址并选择 OAuth。若只支持本地命令、没有远程 HTTP 或 OAuth 登录功能，则不能按本指南直接连接。找不到添加入口时，请先确认你的套餐和组织管理员是否允许自定义 MCP。</p><p>遇到需要手工填写 Client ID / Client Secret 的界面，请联系管理员核对该客户端的注册方式；不要填写看板密码或其他系统的密钥。</p></details></section>
<section id="examples"><h2>3. 直接复制这些问题</h2><p>第一次使用先确认账户与覆盖日期。提问时写明账户、日期和要比较的指标。所有日期按北京时间；“上周”应解析成具体的周一至周日。</p>
<h3>第一次连接：确认可以查询什么</h3><blockquote>请使用 Baidu Ads MCP 列出我可以访问的账户，并检查各账户的数据覆盖日期与缓存状态。先告诉我有哪些数据可用、哪些缺失，不要把缺失数据当作 0。</blockquote>
<h3>钱花在哪里</h3><blockquote>请分析 TinaCMS 上一个完整周的广告表现，先写出北京时间起止日期。列出花费、点击、展示、平均点击费用，并找出花费最多的 10 个搜索词。结合搜索意图判断是不是目标用户，不要只根据点击率下结论。给出数据来源、覆盖范围和最多 3 条建议。</blockquote>
<h3>对比上周与前一周</h3><blockquote>比较 SSW 上一个完整周与前一个完整周的花费、点击和付费联系点击会话数。先检查两段日期是否完整；如不完整，明确指出，不要直接得出增长或下降结论。将广告数据和网站转化分别说明。</blockquote>
<h3>指定日期，与看板核对</h3><p class="muted">下面日期仅为格式示例，使用时替换为有数据覆盖的完整日期。</p><blockquote>查询 TinaCMS 在北京时间 2026-11-09 至 2026-11-15 的总花费、点击、展示和付费邮件咨询点击会话数。不要筛选单一广告计划。列明日期、账户、筛选条件、数据版本和统计口径，便于我与广告看板核对。</blockquote>
<h3>网站与安装行为</h3><blockquote>查询 TinaCMS 最近 7 个完整日期的网站访问和百度付费安装命令复制成功会话数。分开说明全站访问、付费目标会话和中国 CLI 事件；不要把复制成功当成安装完成，也不要把所有 CLI 事件归因于广告。</blockquote>
<h3>微信客服咨询</h3><blockquote>查询 SSW 最近 7 个完整日期的微信客服咨询客户数与客户发送消息数，分别说明各指标的覆盖日期。将它们作为全来源客服数据，不要称作百度广告带来的新增客户。</blockquote>
<h3>需要改广告时</h3><blockquote>根据 YakShaver 可用的最近一个完整周数据，提出最多 3 条人工优化建议，附对应搜索词和证据。只分析，不执行广告修改。</blockquote></section>
<section id="scope"><h2>4. 正确理解数据与权限</h2><table><thead><tr><th scope="col">内容</th><th scope="col">如何理解</th></tr></thead><tbody><tr><td>账户</td><td>TinaCMS、SSW、YakShaver，实际可用范围以你的授权为准。</td></tr><tr><td>数据时效</td><td>查询已采集报表的快照，不是实时访问广告后台。先检查覆盖日期与缓存状态；缓存过期时请管理员刷新。</td></tr><tr><td>转化</td><td>付费目标统计的是发生指定行为的会话，不是订单或新增客户；账户级目标不能直接分摊到某个关键词。</td></tr><tr><td>全站指标</td><td>全站访问与百度付费流量分开。日 UV、IP 数和平均时长不能直接相加成任意日期范围的总值。</td></tr><tr><td>私有汇总</td><td>中国 CLI 和微信客服数据需要登录授权。客户端会将查询结果提供给其 AI 模型，请仅在公司允许的客户端中使用，不要公开分享结果。</td></tr><tr><td>操作权限</td><td>当前同事访问按查询配置。导出、订阅和管理操作另需授权；生产广告执行与自动修改开关保持关闭。</td></tr></tbody></table><p><a href="/activity">操作历史</a>中可以查看自己的调用记录；管理员可查看全部。记录包括时间、操作及结果状态，不保存完整问题或响应内容。此页面需要单独登录。</p></section>
<section id="help"><h2>5. 常见问题</h2><details><summary>连接成功，但 AI 没有查询数据</summary><p>确认当前对话启用了 Baidu Ads 工具，并明确说“请使用 Baidu Ads MCP 查询”。检查客户端的工具调用记录；没有调用结果时，不应把 AI 的猜测当作报表数据。</p></details><details><summary>登录失效、授权超时或权限不足</summary><p>回到客户端重新发起连接和授权，不要重复使用旧的授权网页。确认使用本人账号。若仍提示权限不足，将客户端名称和错误代码交给管理员，不要发送密码或令牌。</p></details><details><summary>数据过期、日期缺失或某个指标没有值</summary><p>让 AI 先检查数据状态，再联系管理员核对采集及缓存。刷新 MCP 缓存不等于重新采集广告数据。不要改日期掩盖缺失，也不要将空值当成零。</p></details><details><summary>为什么与看板数字不同？</summary><p>逐项对齐账户、北京时间起止日期、广告计划筛选、数据版本和指标口径。付费转化与全站转化不是同一范围；CLI 与微信客服是全来源背景数据。仍有差异时，保留两边的查询条件和错误信息供管理员核对。</p></details><details><summary>浏览器打开 /mcp 显示未授权</summary><p>这是工具接口，必须由客户端携带授权调用。阅读说明请使用本页；查看图表请打开<a href="https://ads.ssw.com.cn/">广告看板</a>。</p></details></section>
<footer>使用指南 · 更新于 2026-11-18 · 客户端界面可能随版本变化，以链接中的官方文档为准。本页示例仅包含问题，不包含实际业务数据。</footer>
'''

def guide_page():
    digest = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()
    return HTMLResponse('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MCP 使用指南 · Baidu Ads</title><style>'+STYLE+'</style></head><body><main>'+BODY+'</main></body></html>', headers={
        'Cache-Control':'no-store',
        'Content-Security-Policy':"default-src 'none'; style-src 'sha256-"+digest+"'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        'Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'})
