# GM-All-In-One: GameMale 全自动化任务引擎

基于 Python 与 GitHub Actions 构建的 GameMale 论坛零干预自动化打工脚本。在继承前人优秀设计的基础上，对底层 API 接口与正则匹配规则进行了深度重构，并新增了资产监控与邮件推送模块。

## ✨ 核心特性

* **多维任务全覆盖**：无头纯接口执行基础签到、日常抽奖、串门互访（3次）、打招呼（3次）、日志表态（10次）、你画我猜（免图合规出题）。
* **动态资产追踪**：任务执行完毕后，自动解析积分中心，生成当前的个人资产清单（金币、血液、灵魂等）。
* **加密邮件推送**：支持通过标准 SMTP 协议（如 QQ 邮箱、网易邮箱）将每次的执行战报及资产状况推送至指定邮箱。
* **深度脱敏与隐私防护**：严格依赖 GitHub Secrets 传参。代码原生剥离了所有本地调试型日志及动态会话凭证（如 `formhash`），彻底杜绝跨站脚本提权风险。

## 🤝 致谢与灵感来源

本项目的架构与核心逻辑参考并致敬了社区中多位开发者的优秀开源工作：
1. 最初的 GitHub Actions 自动化部署思路由论坛成员 **@thh866** 提出。
2. 基础的日志脱敏逻辑及卡片抽奖模块衍生自 **exact-emote-granny/Gizmo** 项目。

## 🛠️ 部署指南

### 一、 仓库基础配置
1. 点击右上角 **Fork** 本仓库到个人的 GitHub 账号。
2. **【极度重要】** 前往仓库的 `Settings` -> `General` -> `Danger Zone`，将仓库更改为 **Private (私有)** 以保障最高级别的隐私安全。
3. （可选）进入 `Settings` -> `Actions` -> `General` -> `Workflow permissions`，修改为 **Read and write permissions**，以便后续配置防休眠脚本。

### 二、 环境变量配置 (Encrypted Secrets)
前往仓库的 `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`，依次添加以下密钥参数：

| 密钥名称 | 必填 | 作用描述 | 示例值 |
| :--- | :--- | :--- | :--- |
| `USERNAME` | **是** | 论坛登录账号（支持中英文） | `YourUsername` |
| `PASSWORD` | **是** | 论坛登录密码 | `YourPassword` |
| `SMTP_HOST` | 否 | 发件邮箱的 SMTP 服务器地址 | `smtp.qq.com` |
| `SMTP_PORT` | 否 | 发件邮箱的 SSL 加密端口 | `465` |
| `MAIL_USER` | 否 | 用于发送通知的邮箱账户 | `example@qq.com` |
| `MAIL_PASS` | 否 | 邮箱开通 SMTP 后获得的授权码 | `xxxxccccvvvvbbbb` |
| `MAIL_TO` | 否 | 接收报告的个人邮箱（不填则默认发给自己）| `receive@gmail.com` |

### 三、 激活并运行
1. 进入 `Actions` 选项卡，点击绿色的 `I understand my workflows, go ahead and enable them`。
2. 在左侧选中 `GameMale Auto Sign-in`，点击右侧的 **Run workflow** 手动触发首次运行。
3. 检查运行日志与收件箱，确认部署无误即可享受每天的自动化服务。
