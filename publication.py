"""Make the hosted page's privacy and update explanations accurate."""
REPLACEMENTS={
 '数据保存在本机':'公开共享数据快照',
 '每日 09:00 自动采集':'每日 09:00 云端采集',
 '北京时间 · 通过 Codex 运行':'北京时间 · GitHub Actions',
 '北京时间，每日采集截至昨天的数据，回补最近7天以覆盖迟到转化。自动任务通过 Codex 在本机运行，需要本机与 Codex 可运行且有网络。页面为本地快照；采集完成后点“刷新报表”':'北京时间，GitHub Actions 计划每日 09:00 采集截至昨天的数据，回补最近7天；GitHub 调度可能延迟，无需本机开机。页面是最近成功发布的数据快照；请查看最近运行时间并刷新报表',
 '数据来源：百度营销官方 API · 当前账户：智鹏瑞尔 · 数据与分析均保存在本机':'数据来源：百度营销官方 API · 当前账户：智鹏瑞尔 · 本页及所展示统计数据可公开访问',
 '；临近到期会提醒，需在百度后台续期并更新本机凭据。页面仅包含报表数据，不包含令牌。CSV、SQLite、程序与本页都保存在 Baidu.Ads 文件夹。':'；到期前需在百度后台续期并更新 GitHub Actions Secrets。采集令牌不会发送到浏览器。仓库包含可公开的统计数据及加密原始备份，访客 IP 不进入公开页面或明文数据。',
}
def public_template(source):
 for old,new in REPLACEMENTS.items():
  if old not in source:raise ValueError('Publication text changed; review privacy wording before publishing')
  source=source.replace(old,new)
 return source
