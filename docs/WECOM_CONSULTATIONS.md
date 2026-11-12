# SSW 微信客服咨询统计

SSW 看板使用微信客服统计接口 `kf/get_corp_statistic` 的 `customer_cnt`。数据按北京时间和指定客服账号读取。选定多天时将各天人数相加，同一客户在多天咨询会分别计入。一个日期只有在接口返回人数时才算已覆盖。看板在收到首份加密快照后显示微信客服卡片。

## 首次设置

1. 在智鹏瑞尔企业微信后台，确认 **SSW 客服统计** 已出现在 **App Management → WeChat Customer Service → API → Apps that can call APIs**。在该自建应用中配置调用服务器实际使用的公网出口 IP。核对 `ssw.com.cn` 当前客服账号的 `open_kfid`。
2. 在已列为可信 IP 的阿里云 Windows 服务器上，用运行计划任务的 Windows 账号检出本仓库，安装 Python 3.12 或更新版本，创建虚拟环境。

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
   ```

3. 准备企业 ID、自建应用 Secret、客服账号 `open_kfid` 和一把独立的随机 32 字节查看密钥（64 个十六进制字符）。将查看密钥保存在个人密码管理器。GitHub fine-grained PAT 仅选择 `hveraus/Baidu.Ads`，权限为 **Contents: Read and write**。该令牌在 GitHub 的授权范围覆盖整个仓库内容。Windows 配置向导会隐藏三个密钥的输入，使用 DPAPI 加密保存，仅同一机器上的同一 Windows 账号可解密。

   ```powershell
   powershell.exe -NoProfile -File .\scripts\run_wecom_consultations.ps1 -Initialize
   powershell.exe -NoProfile -File .\scripts\run_wecom_consultations.ps1 -Check
   ```

4. 核对检查显示 `WeCom statistics API OK` 且有数据日期，再手动运行一次发布。

   ```powershell
   powershell.exe -NoProfile -File .\scripts\run_wecom_consultations.ps1
   ```

   成功后确认 `main` 上的 `accounts/ssw/data/wecom-consultations.enc` 有更新，且 **Publish GitHub Pages** 工作流已部署。打开 SSW 总览，使用独立查看密钥解锁，选择同一北京时间日期和客服账号，与企业微信后台 **Data Statistics → Number of Customers Asking for Consultations** 对账。刷新页面后需再次解锁。

## 每日更新

在 Windows 任务计划程序中为上述 Windows 账号创建任务，建议北京时间每天 08:30 执行。配置绝对路径，启用漏跑后补跑、失败重试以及已有实例运行时跳过重复启动。

| 项目 | 值 |
|---|---|
| 程序 | `powershell.exe` |
| 参数 | `-NoProfile -File "<检出目录>\scripts\run_wecom_consultations.ps1"` |
| 起始于 | 检出目录 |

采集器查询最近 90 个可用的完整北京时间日期，只发布按日聚合加密快照。API 失败或完全没有返回计数时，上一份快照继续可用。GitHub 令牌到期或需要更换凭据时，使用同一 Windows 账号重新运行 `-Initialize`。
