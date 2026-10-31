# GM Cookie Helper · 隐私政策 / Privacy Policy

最后更新：2026-10-31

---

## 中文

### 一、这个扩展是做什么的

GM Cookie Helper 是一个浏览器扩展（Manifest V3），只有两个用途：

1. 在用户**本人已登录** GameMale 论坛的前提下，读取该站点自己的登录 Cookie，
   供用户粘贴到自己的 GitHub 仓库 Secret 中；
2. 在用户**自己提供** GitHub 个人访问令牌（PAT）的前提下，帮用户在自己的 GitHub
   账号下创建仓库、写入工作流所需的 Secrets、开启 Actions 写权限。

它不提供任何内容、不做广告、不含统计埋点、不加载任何远程代码。

### 二、处理哪些数据

| 数据 | 来源 | 用途 | 存放位置 |
| :-- | :-- | :-- | :-- |
| GameMale 的登录 Cookie（含 HttpOnly 的 `auth`） | 从用户已登录的浏览器读取 | 写入用户自己仓库的 `GM_USER_COOKIE` Secret | 仅本机 `chrome.storage.local`，上传前用仓库公钥密封 |
| GitHub PAT | 用户手动填写 | 调用 GitHub API 建仓库、写 Secret、触发工作流 | 仅本机 `chrome.storage.local` |
| 邮箱地址 / SMTP 授权码 | 用户手动填写 | 写入用户自己仓库的 `GM_MAIL_*` Secret，用于接收报告邮件 | 仅本机 `chrome.storage.local`，上传前用仓库公钥密封 |
| GameMale 账号密码（可选） | 用户手动填写 | 仅用于写入 `GM_PASSWORD` Secret | 同上 |

### 三、数据发往哪里

**唯一的网络出口是 `https://api.github.com`**，且使用的是用户自己填写的 PAT，
操作对象是用户自己的仓库。除此之外没有任何第三方服务器。

本项目没有自建后端，作者**无法**访问任何用户的数据。

Cookie、邮箱密码等敏感值在写入 Secret 之前，会先用目标仓库的公钥做
`crypto_box_seal` 加密（`tweetnacl` + `blakejs`，均已随扩展打包，非远程加载），
GitHub 侧也只能存成加密后的 Secret。

### 四、数据保留与删除

- 所有配置都保存在浏览器本地（`chrome.storage.local`）。在扩展弹窗里点「清空本地配置」，
  或在浏览器扩展管理页移除本扩展，即可全部删除。
- 已写入用户 GitHub 仓库的 Secret，由用户在自己的仓库
  `Settings → Secrets and variables → Actions` 中删除。

### 五、不收集什么

不收集浏览历史、不收集其他网站的 Cookie、不收集设备指纹、不做用户画像、
不向任何分析服务或广告网络发送数据。

### 六、联系方式

问题或数据删除请求请提 Issue：
<https://github.com/Highboed/GM-All-In-One/issues>

---

## English

### What this extension does

GM Cookie Helper is a Manifest V3 browser extension with exactly two purposes:

1. Read the user's **own** GameMale forum login cookies (the user must already be
   signed in) so they can be stored as a Secret in the user's own GitHub repository.
2. Using a GitHub Personal Access Token **supplied by the user**, create a repository
   under the user's own account, write the required Action Secrets, and enable
   workflow write permissions.

It serves no content, shows no ads, contains no analytics, and loads no remote code.

### Data processed

| Data | Source | Purpose | Storage |
| :-- | :-- | :-- | :-- |
| GameMale login cookies (incl. HttpOnly `auth`) | Read from the user's signed-in browser | Written to `GM_USER_COOKIE` in the user's own repo | Local `chrome.storage.local`; sealed with the repo public key before upload |
| GitHub PAT | Entered by the user | Call GitHub API to create repo, write Secrets, dispatch workflows | Local `chrome.storage.local` |
| Email address / SMTP password | Entered by the user | Written to `GM_MAIL_*` Secrets to deliver report emails | Local `chrome.storage.local`; sealed before upload |
| GameMale password (optional) | Entered by the user | Written to the `GM_PASSWORD` Secret | Same as above |

### Where data goes

The **only** network destination is `https://api.github.com`, authenticated with the
user's own token and acting on the user's own repository. There is no third-party
server and no backend operated by the developer — the developer cannot access any
user data.

Sensitive values are sealed with `crypto_box_seal` using the target repository's
public key before being written as Secrets (`tweetnacl` and `blakejs` are bundled
with the extension, never loaded remotely).

### Retention and deletion

All settings live in `chrome.storage.local`. Use "clear local data" in the popup, or
remove the extension, to delete everything locally. Secrets already written to the
user's repository can be deleted from that repository's
`Settings → Secrets and variables → Actions`.

### Not collected

No browsing history, no cookies from other sites, no device fingerprinting, no
profiling, no analytics or advertising SDKs.

### Contact

<https://github.com/Highboed/GM-All-In-One/issues>
