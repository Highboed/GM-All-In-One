# GM-All-In-One Gamemale论坛签到一条龙

<img width="2752" height="1536" alt="banner" src="https://github.com/user-attachments/assets/67b7061a-144d-4a4c-bfd1-d550ea9bf19d" />


这是一个用于 GameMale 论坛的纯后台自动化挂机脚本。
配置好之后，它每天会定时帮你完成签到、抽奖、互动等全部日常，并把当天的签到情况与资产变动排版成邮件发给你。

---

## 🚀 部署教程

整个部署过程大概需要 3 分钟，只需要在 GitHub 网页上点几下，不需要你自己有服务器。

### 第一步：Fork 并隐藏仓库（极其重要）
1. 点击本页面右上角的 **Fork**，把项目复制到你自己的 GitHub 账号下。
2. ⚠️ **保护隐私**：Fork 完成后，立刻进入你仓库的 `Settings` -> 左侧选 `General` -> 滑到页面最底部的 `Danger Zone`，点击 **Change visibility**，将仓库改为 **Private（私有）**。


### 第二步：获取发件邮箱授权码
为了让脚本能给你发邮件通知，你需要一个发件邮箱（比如 QQ 邮箱）。
1. 登录 QQ 邮箱网页版，进入 `设置` -> `账号与安全`。
2. 往下翻找到 `POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务`。
3. 开启 **SMTP 服务**，点击“生成授权码”，把弹出的那一串十多位的字母密码**复制保存下来**（不要告诉别人）。
<img width="2088" height="1056" alt="image" src="https://github.com/user-attachments/assets/ffe595b2-392e-4d17-8723-c80242cc7621" />


### 第三步：填入账号密码配置
回到你的 GitHub 仓库页面，进入 `Settings` -> 左侧找 `Secrets and variables` -> 点 `Actions`。
点击绿色的 **New repository secret** 按钮，只需要添加以下 **5个核心变量** 即可：

| 变量名 (Name) | 填什么 (Secret) |
| :--- | :--- |
| `USERNAME` | 你的 GameMale 论坛账号名 |
| `PASSWORD` | 你的论坛登录密码 |
| `SMTP_HOST` | `smtp.qq.com` (如果你用的是QQ邮箱) |
| `MAIL_USER` | 你的发件邮箱（比如 123456@qq.com） |
| `MAIL_PASS` | **刚才第二步获取的那一串字母授权码** |

*(注：如果你想把报告发给别的邮箱，可以再额外添加一个 `MAIL_TO` 变量填入接收邮箱。如果不填，则默认发给自己。)*

### 第四步：给脚本开放写入权限（防断签）
为了让脚本能把每天的金币数量保存下来做对比，以及防止 GitHub 自动休眠你的任务，必须开放权限。
1. 进入 `Settings` -> 左侧选 `Actions` -> 点 `General`。
2. 滑到最底部的 `Workflow permissions`，选中 **Read and write permissions**。
3. 点击 **Save** 保存。
<img width="2109" height="1763" alt="屏幕截图 2026-06-16 211315" src="https://github.com/user-attachments/assets/a9234e3d-f50f-4a43-9256-0833c6fad419" />



### 第五步：一键激活运行
1. 点击仓库顶部的 **Actions** 选项卡。
2. 如果看到提示，点击绿色的 `I understand my workflows, go ahead and enable them` 允许运行。
3. 在左侧菜单点击 `GameMale Auto Sign-in`。
4. 点击右侧灰色的 **Run workflow** 手动触发第一次运行。
5. 等待大概几十秒，看到绿色的打勾，就可以去邮箱查收你的第一份资产看板了！以后每天它都会在云端自动打卡。

---

## ✨ 它到底能干什么？ (功能特性)

本脚本全面摒弃了模拟浏览器的慢速方案，所有操作直接对接论坛底层 API，执行速度极快且稳定：
* **全日常覆盖**：自动执行基础签到、插件抽奖、空间串门(3次)、打招呼(3次)、日志吃瓜表态(10次)。
* **你画我猜 API 化**：通过提交极简的隐形像素图，实现 100% 纯后台静默出题，绕过前端验证限制。
* **资产看板与对比**：每次运行后剥离干扰代码，精准抓取金币、血液、灵魂等硬通货。自动对比昨日数据，算出金币涨幅。
* **高安全性**：代码已做深度脱敏，所有动态 Token（如 formhash）阅后即焚，绝不打印在日志中。
* **自带防休眠**：内置保活工作流，每月自动更新时间戳，破解 GitHub 连续 60 天无活动自动暂停 Actions 的限制。

---

## 🤝 致谢

本项目的诞生离不开社区前辈的开源精神，核心逻辑与灵感参考了以下开发者的工作：
* 特别感谢 **@thh866** 提供的最初 Actions 自动化触发流与部署思路。
* 感谢 **exact-emote-granny/Gizmo** 项目在日志隐私脱敏策略以及日常抽奖模块上的启发。
