# -*- coding: utf-8 -*-
"""
gm_gate.py —— GameMale 站点验证门（dev8133_cloudflare / Turnstile）破解模块
===========================================================================

一、这道「Cloudflare 拦截」到底是什么
------------------------------------
www.gamemale.com 在 Discuz 之上装了第三方插件 `dev8133_cloudflare`，它把**几乎所有前台
入口**（forum.php / member.php / home.php / misc.php / plugin.php / k_misign-sign.html
/ space-uid-*.html …）都拦在一个人机验证页后面：

    <title>请稍候...</title>
    turnstile.render("#turnstile", {sitekey: "0x4AAAAAAEqRGyPbvEznAcKy", appearance: "always"})
    → 拿到 token 后 axios.post("plugin.php?id=dev8133_cloudflare", {token})
    → 服务端调 Cloudflare siteverify，成功返回 {"code":200} 并种下放行 Cookie

所以**它不是 Cloudflare 自带的 "Just a moment" 挑战页**，而是站点自己架的 Turnstile 关卡。
关键推论：
  * token 由 Cloudflare 服务端校验，**无法伪造**（实测伪造返回
    {"code":-1,...,"data":{"error_code":"invalid-input-response"}}）；
  * `appearance: "always"` 强制显示复选框，所以「看起来总是要你点一下」。

二、实测出来的三条边界（很重要，决定了整个方案的形状）
------------------------------------------------------
1. **爬虫 UA 白名单**：插件放行 Googlebot / Bingbot / Baiduspider / Sogou / 360Spider /
   YisouSpider / Bytespider（不放行 YandexBot 与普通浏览器 UA）。用 Baiduspider UA 时，
   forum.php / member.php / home.php / plugin.php?id=k_misign:sign / space-uid-*.html
   **全部 200 且不设防** —— 也就是说白名单 UA 能读完整个站点，连签到插件页都能打开。
2. **`misc.php` 整个文件对全部白名单 UA 返回 403（空 body）**。
   不只是 seccode —— `misc.php?mod=faq` 也是 403，说明拦截粒度是「路径」而不是「参数」。
   大小写 / 双斜杠 / `/.` / `/foo/..` / PATH_INFO / `%20` / POST 全试过，一律 403。
   后果：登录验证码图片拿不到 → **蜘蛛 UA 无法完成登录**。
3. **但白名单 UA 的 POST 是放行的**（这一点极关键）：
       POST /member.php?...loginsubmit=yes  → 200，业务层回「抱歉，验证码填写错误」
       POST /plugin.php?id=k_misign:sign    → 200，业务层回「您所在用户组不允许使用」
   也就是说插件**只挡 misc.php**，写操作本身不挡。
   ⇒ 只要手上有一枚**已登录的 Discuz Cookie**，蜘蛛 UA 就能把整套签到做完，
     **完全不需要 Turnstile、不需要验证码、不需要浏览器**（见下面的「用户 Cookie 模式」）。
4. **移动端 API `api/mobile/index.php` 完全不受门保护**（`module=check` 返回
   `{"testcookie":null}`，`module=register` 返回移动版注册页），
   但 `module=login` / `module=seccode` 返回 **0 字节** —— 站点把这两个模块摘掉了。
5. **过门校验接口 `/plugin.php?id=dev8133_cloudflare` 自己不受门保护**，
   对它 POST 假 token 会返回 `{"code":-1,...,"error_code":"invalid-input-response"}`。
   ⇒ 打码平台（CapSolver）可以在**它自己的 IP** 上解出 token，我们再从任意 IP 提交换放行 Cookie。
     （代价：一条 CapSolver key，约 $0.001/次）

三条可行路线（按推荐度排序，代码里都有）
----------------------------------------
  A. **用户 Cookie 模式**（最稳、免费、零浏览器）
     `GM_USER_COOKIE` = 你自己浏览器里的登录 Cookie（`TVj0_2132_auth` 等，有效期 30 天）
     配好后：蜘蛛 UA 过门 + 已有登录态 ⇒ 只跑业务请求。**完全不碰 Turnstile**。
  B. **打码平台模式**（全自动，付费）
     `CAPSOLVER_KEY` ⇒ 解 Turnstile → 提交校验接口换放行 Cookie → 走 HTTP 登录（验证码交给 ddddocr）。
  C. **浏览器模式**（兜底，最不稳）
     xvfb + 有头 Chrome 现场过 Turnstile。实测受出口 IP 信誉影响很大（见下面第四节）。

⚠️ 关于路线的稳定性（实测结论）
--------------------------------
  * 放行 Cookie 寿命很短：15:22 导出的串，18:25 已失效（同一台机器、同一出口 IP），
    所以**不要指望「本地破门一次、CI 用一个月」**。
  * 本机反复自动化探测后，连**有头浏览器**都过不了门了（Turnstile 一直停在
    interaction_required，且 iframe 不渲染）——出口 IP 信誉被降级。
  * 因此：A > B > C。C 只作为最后的兜底。

三、为什么之前一直失败（真正的根因）
------------------------------------
失败日志里 Turnstile 状态永远停在 `interaction_required`，看起来像「点击没生效」。
实际根因是 **无头模式**：`ChromiumOptions.headless(True)` 带的是旧无头内核，
Cloudflare 一眼识破，于是永远给出交互挑战，而且点击永远不通过。

实测对照：
    headless=True  → 90 秒 × 3 轮，始终 interaction_required，失败
    headless=False → **约 10 秒自动通过，一次点击都不需要**

所以本模块的策略是 **有头优先**（CI 里用 xvfb-run 提供虚拟显示器，等价于有头）。

四、对外接口
------------
    gate = GateKeeper(hostname="www.gamemale.com", logger=logger)
    gate.classify(http)        # 判定门口状态（直连 / 爬虫白名单 / 必须浏览器 / 通不了）
    gate.ensure_access(http)   # 保证 http 引擎已「过门」，必要时起浏览器
    gate.close()               # 释放浏览器

命令行诊断（不需要账号密码）
----------------------------
    python gm_gate.py                      # 探测门口状态
    python gm_gate.py --solve              # 起浏览器实际破门
    python gm_gate.py --solve --headed     # 强制有头（CI 配合 xvfb-run）
    python gm_gate.py --solve --probe-login   # 破门后顺带验证「登录页+验证码图片」是否可达
    python gm_gate.py --solve --export     # 破门后导出 Cookie 串（塞进 GitHub Secret 用）
    python gm_gate.py --capsolver KEY      # 用打码平台直接取 token
"""

import argparse
import base64
import json
import os
import re
import shutil
import sys
import time

# ---------------------------------------------------------------- 站点常量

DEFAULT_HOST = "www.gamemale.com"
GATE_URL = "https://www.gamemale.com/forum.php"

GATE_MARKER = "dev8133_cloudflare"
TURNSTILE_SITEKEY = "0x4AAAAAAEqRGyPbvEznAcKy"
VERIFY_PATH = "/plugin.php?id=dev8133_cloudflare"

# Cookie 复用（CI 免起浏览器）
COOKIE_ENV = "GM_GATE_COOKIE"

# 用户自己的登录 Cookie（最稳的一条路：蜘蛛 UA + 已有登录态，全程不碰 Turnstile）
USER_COOKIE_ENV = "GM_USER_COOKIE"
SPIDER_UA_ENV = "GM_SPIDER_UA"

# 蜘蛛 UA 下被插件封死的路径（前缀匹配）。用到这里要提前报错，别等 403 才猜。
SPIDER_BLOCKED_PATHS = ("misc.php",)

# 登录态判定：Discuz 会在页面里输出 discuz_uid
UID_RE = re.compile(r"discuz_uid\s*=\s*['\"]?(\d+)")

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 实测可通过插件的搜索引擎 UA（用于 classify 判定 + 破门失败时的降级只读）
SPIDER_UAS = [
    ("Baiduspider",
     "Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)"),
    ("Googlebot",
     "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    ("Bingbot",
     "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"),
    ("Sogou",
     "Sogou web spider/4.0(+http://www.sogou.com/docs/help/webmasters.htm#07)"),
    ("360Spider",
     "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/38.0.2125.122 Safari/537.36 360Spider"),
]

# 可选隐身脚本。**默认不注入**：实测有头模式下不注入也能一次过，
# 而 Object.defineProperty(navigator,'webdriver') 这种改法本身就是一个可被检测的特征。
# 只有当你确实需要无头模式且愿意冒风险时，才设 GM_STEALTH=1。
STEALTH_JS = """
try { delete Object.getPrototypeOf(navigator).webdriver; } catch (e) {}
window.chrome = window.chrome || {};
"""

# 门口状态
OPEN = "open"              # 门没开（站点未启用插件），普通 UA 直连即可
SPIDER_OK = "spider"       # 门开着，爬虫白名单可通行（**只能读，无法登录**）
NEED_BROWSER = "browser"   # 必须真实浏览器过 Turnstile
UNKNOWN = "unknown"        # 网络异常，无法判定
USER_COOKIE_MODE = "user_cookie"   # 蜘蛛 UA + 用户自己的登录 Cookie，全程不碰 Turnstile


class GateError(Exception):
    """过门失败。"""


# ---------------------------------------------------------------- Cookie 复用编解码


def encode_cookie_blob(cookies, ua=None):
    """
    把 cookie 字典编码成可塞进 GitHub Secret 的单行字符串。

    必须**连 User-Agent 一起带**：放行 Cookie 是跟浏览器会话绑定的，
    Cookie 与 UA 只要对不上，复验就会被打回验证门。
    """
    payload = {"ua": ua or CHROME_UA, "cookies": cookies}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cookie_blob(blob):
    """解码。返回 (ua, cookies)。兼容早期「裸 cookie 字典」格式。"""
    txt = base64.urlsafe_b64decode(blob.strip().encode("ascii")).decode("utf-8")
    data = json.loads(txt)
    if not isinstance(data, dict):
        raise ValueError("Cookie 串格式不对")
    if isinstance(data.get("cookies"), dict):
        return (data.get("ua") or CHROME_UA), data["cookies"]
    return CHROME_UA, data


def parse_cookie_header(raw):
    """
    把浏览器里复制出来的 `Cookie:` 头解析成字典。

    兼容几种常见粘贴形态：
        a=1; b=2
        Cookie: a=1; b=2
        a=1;\nb=2
    值里含 '=' 也能正确切分（只在第一个 '=' 处切）。
    """
    if not raw:
        return {}
    txt = raw.strip()
    if txt.lower().startswith("cookie:"):
        txt = txt.split(":", 1)[1]
    txt = txt.replace("\r", " ").replace("\n", " ")
    out = {}
    for part in txt.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name:
            out[name] = value.strip()
    return out


def detect_uid(html):
    """从页面里读出 discuz_uid。游客是 0，已登录是真实 uid。读不到返回 None。"""
    m = UID_RE.search(html or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


# ---------------------------------------------------------------- HTTP 引擎


class HttpEngine:
    """
    统一 HTTP 引擎。优先 curl_cffi（带 Chrome 的 TLS/HTTP2 指纹），不可用时回退 requests。
    两者对外接口一致。

    trust_env 默认 False：本机 shell 会强制注入一个不可用的 HTTP_PROXY，
    而 www.gamemale.com 国内直连即可，绝不能被环境变量带偏。
    """

    def __init__(self, hostname=DEFAULT_HOST, logger=None, timeout=30,
                 impersonate="chrome", trust_env=False):
        self.hostname = hostname
        self.logger = logger
        self.timeout = timeout
        self.impersonate = impersonate
        self.trust_env = trust_env
        self.kind = "requests"
        self.session = None
        self.user_agent = CHROME_UA
        self._build_session()

    # -- 内部 ------------------------------------------------------------

    def _log(self, level, msg):
        if self.logger:
            getattr(self.logger, level)(msg)

    def _build_session(self):
        if self.impersonate:
            try:
                from curl_cffi import requests as cffi_requests
                self.session = cffi_requests.Session(
                    impersonate=self.impersonate, trust_env=self.trust_env)
                self.kind = "curl_cffi(%s)" % self.impersonate
            except Exception as exc:  # noqa: BLE001
                self._log("warning", "curl_cffi 不可用(%s)，回退 requests" % exc)
        if self.session is None:
            import requests as _requests
            self.session = _requests.Session()
            if not self.trust_env:
                self.session.trust_env = False
            self.kind = "requests"
        self._apply_default_headers()
        return self.session

    def _apply_default_headers(self):
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,image/apng,*/*;q=0.8"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        })

    # -- 对外 ------------------------------------------------------------

    def set_user_agent(self, ua):
        if ua:
            self.user_agent = ua
            self.session.headers.update({"User-Agent": ua})

    def set_referer(self, url):
        if url:
            self.session.headers.update({"Referer": url})
        return self

    def set_cookies(self, cookies, domain=None):
        """
        把浏览器拿到的 cookie 注入会话。不同引擎的 CookieJar 接口有差异，多策略尝试。
        """
        if not cookies:
            return 0
        domain = domain or ("." + self.hostname if not self.hostname.startswith(".")
                            else self.hostname)
        jar = self.session.cookies
        for name, value in cookies.items():
            for attempt in (
                lambda n=name, v=value: jar.set(n, v, domain=domain, path="/"),
                lambda n=name, v=value: jar.set(n, v, domain=domain),
                lambda n=name, v=value: jar.set(n, v),
            ):
                try:
                    attempt()
                    break
                except Exception:  # noqa: BLE001
                    continue
        try:
            jar.update({k: v for k, v in cookies.items()})
        except Exception:  # noqa: BLE001
            pass
        try:
            return len(self.session.cookies)
        except Exception:  # noqa: BLE001
            return 0

    def cookie_dict(self):
        try:
            return {c.name: c.value for c in self.session.cookies}
        except Exception:  # noqa: BLE001
            return {}

    def cookie_string(self):
        return "; ".join("%s=%s" % (k, v) for k, v in self.cookie_dict().items())

    def clear_cookies(self):
        try:
            self.session.cookies.clear()
        except Exception:  # noqa: BLE001
            pass

    def get(self, url, **kw):
        kw.setdefault("timeout", self.timeout)
        kw.setdefault("allow_redirects", True)
        return self.session.get(url, **kw)

    def post(self, url, **kw):
        kw.setdefault("timeout", self.timeout)
        kw.setdefault("allow_redirects", True)
        return self.session.post(url, **kw)


# ---------------------------------------------------------------- 门口判定


def is_gated(text):
    """响应体是否是那个验证门页面。"""
    if not text:
        return False
    return GATE_MARKER in text


def looks_like_forum(text):
    """响应体是否是真正的论坛页面（而非验证门 / 空壳）。"""
    if not text:
        return False
    if is_gated(text):
        return False
    if "formhash" in text:
        return True
    if "discuz" in text.lower():
        return True
    return len(text) > 20000


# ---------------------------------------------------------------- 门口处理


class GateKeeper:
    """负责识别并破解验证门，把可用会话交给 HttpEngine。"""

    def __init__(self, hostname=DEFAULT_HOST, logger=None,
                 headless=None, browser_path=None,
                 solve_timeout=90, browser_attempts=3,
                 manual=False, export_cookie=False):
        self.hostname = hostname
        self.logger = logger
        self.solve_timeout = solve_timeout
        self.browser_attempts = browser_attempts
        self.manual = manual
        self.export_cookie = export_cookie
        self.browser_path = browser_path or os.getenv("GM_CHROME_PATH") or self.find_chrome()
        self.user_data_dir = os.getenv("GM_USER_DATA_DIR") or None
        self.headless = self._decide_headless() if headless is None else headless
        self.page = None
        self.state = UNKNOWN
        self.spider_ua_name = None
        self._profile_dir = None
        self._passed_via_http = False   # 是否靠「抓 token 走 HTTP」过的门
        self.gate_cookie_blob = None    # 过门后导出的 Cookie 串
        self.gate_mode = None           # 最终采用的过门方式（user_cookie / browser / ...）
        self.logged_uid = None          # 用户 Cookie 模式下的已登录 uid

    def url(self, path):
        """拼绝对地址。path 需以 / 开头。"""
        if path.startswith("http"):
            return path
        return "https://%s%s" % (self.hostname, path)

    # -- 基础 ------------------------------------------------------------

    @staticmethod
    def find_chrome():
        """
        显式定位 Chrome。DrissionPage 默认的 browser_path='chrome' 在部分环境下
        会直接抛 BrowserConnectError，所以这里自己找一遍。
        """
        cands = []
        for name in ("google-chrome", "google-chrome-stable", "chrome", "chrome.exe",
                     "chromium", "chromium-browser"):
            w = shutil.which(name)
            if w:
                cands.append(w)
        if sys.platform.startswith("win"):
            cands += [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
            ]
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
                cands.insert(0, winreg.QueryValueEx(key, "")[0])
            except Exception:  # noqa: BLE001
                pass
        elif sys.platform == "darwin":
            cands.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        else:
            cands += ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                      "/usr/bin/chromium", "/usr/bin/chromium-browser", "/snap/bin/chromium"]
        for c in cands:
            try:
                if c and os.path.exists(c):
                    return c
            except Exception:  # noqa: BLE001
                continue
        return None

    def _log(self, level, msg):
        if self.logger:
            getattr(self.logger, level)(msg)
        else:
            print("[%s] %s" % (level.upper(), msg))

    def _decide_headless(self):
        """
        默认策略：**有头优先**。
        Turnstile 对无头内核的评分极低（实测无头 90 秒×3 轮必失败，有头约 10 秒自动通过）。
        Linux/CI 下如果没有 DISPLAY，才退回无头；CI 里请用 xvfb-run 提供虚拟显示器。
        """
        env = (os.getenv("GM_HEADLESS") or "").strip().lower()
        if env in ("1", "true", "yes", "on"):
            return True
        if env in ("0", "false", "no", "off"):
            return False
        if (os.getenv("GM_HEADFUL") or "").strip().lower() in ("1", "true", "yes", "on"):
            return False
        if sys.platform.startswith("linux"):
            return not bool(os.getenv("DISPLAY"))
        return False

    @property
    def forum_url(self):
        return "https://%s/forum.php" % self.hostname

    # -- 探测 ------------------------------------------------------------

    def _probe(self, http, ua=None):
        """用指定 UA 探一次 forum.php，返回 (状态码, 是否被门拦住, 正文长度)。"""
        headers = {"Referer": "https://%s/" % self.hostname}
        if ua:
            headers["User-Agent"] = ua
        try:
            resp = http.get(self.forum_url, headers=headers)
            text = resp.text or ""
            return resp.status_code, is_gated(text), len(text)
        except Exception as exc:  # noqa: BLE001
            self._log("warning", "探测请求失败: %r" % exc)
            return 0, False, 0

    def classify(self, http=None):
        """
        判定门口形态：
          OPEN         站点未启用插件 / 已有放行 Cookie，普通 UA 直连可用
          SPIDER_OK    开着门，但爬虫 UA 白名单能过（**可读不可登录**）
          NEED_BROWSER 必须真实浏览器过 Turnstile
          UNKNOWN      网络不通，判定不了

        注意：探测过程会临时借用爬虫 UA，结束时必须把原 UA 还原，
        否则后续业务请求会莫名其妙以 Baiduspider 身份发出去。
        """
        own = http is None
        if own:
            http = HttpEngine(self.hostname, self.logger)
            http.set_user_agent(CHROME_UA)

        orig_ua = http.user_agent
        try:
            # 基准探测**显式**用普通浏览器 UA：不能依赖 session 里当前的 UA，
            # 因为前面若借用过蜘蛛 UA（或调用方设置了别的 UA），会把「门是否开着」判错。
            code, gated, size = self._probe(http, ua=CHROME_UA)
            if code == 0:
                self.state = UNKNOWN
                self._log("error", "无法连接 %s，请检查网络" % self.hostname)
                return self.state

            if not gated:
                self.state = OPEN
                self._log("info", "门口状态：OPEN —— 普通 UA 可直连（HTTP %s, %d 字节）"
                          % (code, size))
                return self.state

            self._log("info", "检测到 dev8133_cloudflare 验证门（HTTP %s, %d 字节），"
                              "尝试爬虫白名单..." % (code, size))
            for name, ua in SPIDER_UAS:
                code2, gated2, size2 = self._probe(http, ua=ua)
                if code2 == 200 and not gated2:
                    self.state = SPIDER_OK
                    self.spider_ua_name = name
                    self._log("info", "爬虫白名单命中：%s（HTTP %s, %d 字节）—— 页面与 POST "
                                      "都放行，但 misc.php 整个文件 403，"
                                      "所以拿不到登录验证码；要登录需 %s 或过 Turnstile"
                                      % (name, code2, size2, USER_COOKIE_ENV))
                    break
                self._log("debug", "  白名单未命中: %s (HTTP %s)" % (name, code2))
            else:
                self.state = NEED_BROWSER
                self._log("info", "门口状态：NEED_BROWSER —— 必须用真实浏览器过 Turnstile")
            return self.state
        finally:
            if http.user_agent != orig_ua:
                http.set_user_agent(orig_ua)

    # -- 浏览器破门 -------------------------------------------------------

    def _build_options(self, udd=None):
        from DrissionPage import ChromiumOptions

        co = ChromiumOptions()
        if self.browser_path:
            co.set_browser_path(self.browser_path)
        co.set_argument("--disable-blink-features", "AutomationControlled")
        co.set_argument("--lang", "zh-CN")
        co.set_argument("--window-size", "1280,900")
        co.set_argument("--disable-notifications")
        co.set_argument("--disable-popup-blocking")
        co.set_argument("--no-first-run")
        co.set_argument("--no-default-browser-check")
        if self.headless:
            # Chrome 132+ 的 --headless 已是新内核；显式写 =new 更保险
            co.set_argument("--headless=new")
            co.set_argument("--disable-gpu")
        if os.getenv("GM_CHROME_NO_SANDBOX", "").strip() in ("1", "true", "yes"):
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
        co.set_timeouts(base=20, page_load=60, script=30)
        proxy = os.getenv("GM_CHROME_PROXY")
        if proxy:
            co.set_proxy(proxy)
        if udd:
            co.set_user_data_path(udd)
        return co

    def _launch_browser(self):
        """
        启动浏览器。

        注意 1：实测（Chrome 153 + DrissionPage 4.1.1.4）显式 set_user_data_path() 会导致
        ChromiumPage 抛 BrowserConnectError —— 默认不指定 profile，交给 DrissionPage 自管。
        注意 2：不注入任何 JS（见 STEALTH_JS 注释）。
        """
        from DrissionPage import ChromiumPage

        if self.page is not None:
            return self.page

        candidates = []
        if self.user_data_dir:
            candidates.append(self.user_data_dir)
        candidates.append(None)

        last_exc = None
        for udd in candidates:
            co = self._build_options(udd)
            self._log("info", "启动浏览器（headless=%s, path=%s, profile=%s, proxy=%s）"
                      % (self.headless, self.browser_path or "自动查找",
                         udd or "临时目录", os.getenv("GM_CHROME_PROXY") or "无"))
            try:
                self.page = ChromiumPage(co)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                self._log("warning", "启动失败（profile=%s）: %r" % (udd or "临时目录", exc))
                if udd is not None:
                    self._log("warning", "改用 DrissionPage 自管临时 profile 重试")
                continue
            if (os.getenv("GM_STEALTH") or "").strip() in ("1", "true", "yes"):
                try:
                    self.page.add_init_js(STEALTH_JS)
                except Exception as exc:  # noqa: BLE001
                    self._log("warning", "注入隐身脚本失败（不影响主流程）: %r" % exc)
            self._profile_dir = udd
            return self.page

        self.page = None
        raise last_exc if last_exc else RuntimeError("浏览器启动失败")

    # 页面内探针。**必须把 return 写在脚本顶层**：DrissionPage 会把脚本包进函数体，
    # 写成 (function(){...})() 这种 IIFE 会返回 None。
    _STATE_JS = ("var m=document.getElementById('check_msg');"
                 "return m ? m.innerText.replace(/\\s+/g,' ').trim() : '';")

    _TOKEN_JS = ("var i=document.querySelector('input[name=\"cf-turnstile-response\"]');"
                 "return i ? (i.value || '') : '';")

    _GBOX_JS = """
var f = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
if (!f) return '';
var r = f.getBoundingClientRect();
return JSON.stringify({x: r.x, y: r.y, w: r.width, h: r.height});
"""

    def _check_msg(self, page):
        # 只读 #check_msg 的「渲染后文本」，不能拿页面 HTML 判断状态
        # —— 门页面的 <script> 源码里就写着"验证组件加载失败/人机验证失败"，
        #    用 HTML 判断会永久误报，导致一直刷新、Turnstile 永远没机会完成。
        try:
            return (page.run_js(self._STATE_JS) or "").strip()
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "读取 check_msg 失败: %r" % exc)
            return ""

    def _read_token(self, page):
        """读 Turnstile 已经把 token 塞回来的隐藏域（拿到就能自己交给服务端）。"""
        try:
            return (page.run_js(self._TOKEN_JS) or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _classify_msg(msg):
        if not msg:
            return "unknown"
        if "正在校验" in msg:
            return "verifying"
        if "校验成功" in msg:
            return "success"
        if "请完成上方人机验证" in msg:
            return "interaction_required"
        if "人机验证失败" in msg:
            return "rejected"
        if "验证组件加载失败" in msg:
            return "widget_error"
        if "验证已过期" in msg:
            return "expired"
        if "验证超时" in msg:
            return "timeout"
        if "网络异常" in msg:
            return "network_error"
        if "检查" in msg and "安全性" in msg:
            return "idle"
        return "other"

    def _human_click(self, page, x, y):
        """
        像人一样点击：先分几步把鼠标移过去，再按下、松开。
        Turnstile 对「凭空出现在目标点的一次点击」打分很低。
        """
        sx, sy = max(0.0, x - 160), max(0.0, y - 90)
        steps = 8
        for i in range(1, steps + 1):
            ix = sx + (x - sx) * i / float(steps)
            iy = sy + (y - sy) * i / float(steps)
            page.run_cdp("Input.dispatchMouseEvent", type="mouseMoved",
                         x=int(ix), y=int(iy), button="none", buttons=0)
            time.sleep(0.03)
        time.sleep(0.15)
        page.run_cdp("Input.dispatchMouseEvent", type="mouseMoved",
                     x=int(x), y=int(y), button="none", buttons=0)
        time.sleep(0.1)
        page.run_cdp("Input.dispatchMouseEvent", type="mousePressed",
                     x=int(x), y=int(y), button="left", buttons=1, clickCount=1)
        time.sleep(0.08)
        page.run_cdp("Input.dispatchMouseEvent", type="mouseReleased",
                     x=int(x), y=int(y), button="left", buttons=0, clickCount=1)

    def _try_click_turnstile(self, page):
        """Turnstile 要交互时，复选框在跨域 iframe 内。多策略尝试，全部失败也不报错。"""
        raw = None
        try:
            raw = page.run_js(self._GBOX_JS)
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "读取 turnstile iframe 位置失败: %r" % exc)
        box = None
        if raw:
            try:
                box = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:  # noqa: BLE001
                box = None
        if not box or not box.get("w"):
            self._log("debug", "未定位到 Turnstile iframe（raw=%r）" % (raw,))
            return False

        # 策略 1：CDP 真人轨迹点击 iframe 左侧复选框（Turnstile 复选框贴左边）
        cx, cy = box["x"] + 30, box["y"] + box["h"] / 2.0
        try:
            self._human_click(page, cx, cy)
            self._log("info", "已点击 Turnstile 复选框 (%.0f, %.0f)，iframe 位于 "
                              "(%.0f, %.0f) %.0fx%.0f"
                      % (cx, cy, box["x"], box["y"], box["w"], box["h"]))
            return True
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "坐标点击失败: %r" % exc)

        # 策略 2：让 DrissionPage 直接点 iframe 元素
        try:
            el = page.ele('css:iframe[src*="challenges.cloudflare.com"]', timeout=2)
            if el:
                el.click()
                self._log("info", "已通过元素点击 Turnstile 复选框")
                return True
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "元素点击失败: %r" % exc)
        return False

    def _read_cookies(self, page):
        """多版本兼容地读浏览器 Cookie。"""
        for getter in (
            lambda: page.cookies(all_domains=True),
            lambda: page.cookies(),
            lambda: page.get_cookies(all_domains=True),
            lambda: page.get_cookies(),
        ):
            try:
                cl = getter()
            except Exception:  # noqa: BLE001
                continue
            try:
                d = cl.as_dict()
                if d:
                    return d
            except Exception:  # noqa: BLE001
                pass
            try:
                d = {}
                for c in cl:
                    if isinstance(c, dict):
                        n, v = c.get("name"), c.get("value")
                    else:
                        n, v = getattr(c, "name", None), getattr(c, "value", None)
                    if n:
                        d[n] = v
                if d:
                    return d
            except Exception:  # noqa: BLE001
                continue
        return {}

    def _submit_token(self, http, token):
        """
        自己把 token 交给站点校验接口。
        好处：即使门页面自己的 axios 调用失败（跨域/CSP/时序），我们拿到 token 也能过门。
        """
        http.set_user_agent(CHROME_UA)
        http.set_referer(self.forum_url)
        url = "https://%s%s" % (self.hostname, VERIFY_PATH)
        resp = http.post(url, data={"token": token}, headers={
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://%s" % self.hostname,
        })
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            self._log("debug", "校验接口返回非 JSON: %s" % (resp.text or "")[:200])
            return False
        if data.get("code") == 200:
            return True
        self._log("debug", "校验接口拒绝: %s" % data)
        return False

    def _wait_pass(self, page, timeout, http=None):
        """
        轮询等待验证门通过。状态一律以 #check_msg 的渲染文本为准。
        额外兜底：一旦抓到 Turnstile token，就自己用 HTTP 提交给站点校验接口。
        """
        deadline = time.time() + timeout
        click_budget = 2
        reloads = 0
        max_reloads = 3
        last_action = time.time()
        last_logged = None
        token_tried = False

        while time.time() < deadline:
            try:
                html = page.html or ""
            except Exception as exc:  # noqa: BLE001
                self._log("debug", "读取页面失败: %r" % exc)
                time.sleep(1)
                continue

            if not is_gated(html):
                self._log("info", "验证门已通过（页面标题: %s）" % (page.title or "?"))
                return True

            msg = self._check_msg(page)
            state = self._classify_msg(msg)
            if state != last_logged:
                self._log("info", "Turnstile 状态: %s | 页面提示: %s"
                          % (state, msg[:70] or "(空)"))
                last_logged = state

            # 1) 提示要交互 → 点它
            if state == "interaction_required" and click_budget > 0:
                if self._try_click_turnstile(page):
                    click_budget -= 1
                    last_action = time.time()
                    time.sleep(3)
                    continue

            # 2) 只要拿到 token，就自己提交（不依赖页面自己的 axios）
            if http is not None and not token_tried:
                token = self._read_token(page)
                if token:
                    token_tried = True
                    self._log("info", "抓到 Turnstile token（%d 字符），改由 HTTP 提交校验接口"
                              % len(token))
                    try:
                        self._merge_browser_cookies(page, http)
                        if self._submit_token(http, token):
                            self._log("info", "站点校验通过（code=200）")
                            self._passed_via_http = True
                            time.sleep(1)
                            resp = http.get(self.forum_url)
                            if looks_like_forum(resp.text or ""):
                                self._log("info", "HTTP 引擎已过门（%d 字节）"
                                          % len(resp.text or ""))
                                return True
                    except Exception as exc:  # noqa: BLE001
                        self._log("warning", "token 提交失败: %r" % exc)

            # 3) 真错误 → 刷新重来
            if state in ("rejected", "expired", "widget_error", "timeout", "network_error"):
                if state == "widget_error":
                    self._log("warning",
                              "Turnstile 组件加载失败，通常说明浏览器访问不了 "
                              "challenges.cloudflare.com。本地（国内网络）请设置 "
                              "GM_CHROME_PROXY=http://127.0.0.1:7897；GitHub Actions 无需代理")
                if reloads < max_reloads and (time.time() - last_action) > 8:
                    reloads += 1
                    self._log("info", "刷新页面重新挑战（第 %d/%d 次）" % (reloads, max_reloads))
                    try:
                        page.refresh()
                    except Exception:  # noqa: BLE001
                        pass
                    last_action = time.time()
                    last_logged = None
                    token_tried = False
                    time.sleep(4)
                    continue

            # 4) 长时间无进展 → 刷新一次，避免卡死
            if (time.time() - last_action) > 40 and reloads < max_reloads:
                reloads += 1
                self._log("info", "等待较久仍无进展，刷新重试（第 %d/%d 次）"
                          % (reloads, max_reloads))
                try:
                    page.refresh()
                except Exception:  # noqa: BLE001
                    pass
                last_action = time.time()
                last_logged = None
                token_tried = False

            time.sleep(1)
        return False

    def _merge_browser_cookies(self, page, http):
        cookies = self._read_cookies(page)
        if cookies:
            http.set_cookies(cookies)
            http.set_user_agent(CHROME_UA)
        return cookies

    def solve_with_browser(self, http):
        """起浏览器过 Turnstile，成功后把 Cookie / UA 交给 http。"""
        try:
            page = self._launch_browser()
        except Exception as exc:  # noqa: BLE001
            raise GateError(
                "浏览器启动失败: %r\n"
                "  排查建议：\n"
                "  1) 确认已安装 Chrome（CI 用 ubuntu-latest 自带 google-chrome）\n"
                "  2) 用 GM_CHROME_PATH 显式指定 chrome 可执行文件路径\n"
                "  3) 本地 Windows 可先跑 `python gm_gate.py` 看探测结果\n"
                "  4) 若以 root 运行，设置 GM_CHROME_NO_SANDBOX=1" % exc)

        if self.headless:
            self._log("warning", "当前是无头模式。实测无头几乎必失败，"
                                 "CI 请用 `xvfb-run -a python gamemale.py` 提供虚拟显示器")

        for attempt in range(1, self.browser_attempts + 1):
            self._log("info", "浏览器破门 第 %d/%d 次尝试..." % (attempt, self.browser_attempts))
            try:
                page.get(self.forum_url)
            except Exception as exc:  # noqa: BLE001
                self._log("warning", "打开页面失败: %r" % exc)
                time.sleep(2)
                continue

            if self.manual:
                self._log("info", "人工模式：请在浏览器窗口里手动完成人机验证（最多 %d 秒）"
                          % self.solve_timeout)

            if self._wait_pass(page, self.solve_timeout, http=http):
                return self._harvest(page, http, replace=not self._passed_via_http)

            self._log("warning", "第 %d 次仍在验证门内，准备重试" % attempt)
            try:
                page.refresh()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)

        raise GateError("浏览器连续 %d 次未能通过 Turnstile 验证门"
                        "（提示：无头模式基本过不了，请用有头或 xvfb-run）"
                        % self.browser_attempts)

    def _harvest(self, page, http, replace=True):
        """把浏览器的 Cookie 和 User-Agent 同步给 HTTP 引擎，并复验一次。"""
        cookie_dict = self._read_cookies(page)
        try:
            ua = page.user_agent or CHROME_UA
        except Exception:  # noqa: BLE001
            ua = CHROME_UA

        if replace:
            http.clear_cookies()
        n = http.set_cookies(cookie_dict)
        http.set_user_agent(ua)
        http.set_referer(self.forum_url)

        names = ", ".join(sorted(cookie_dict.keys()))
        self._log("info", "已接管浏览器会话：Cookie %d 项 / UA %s" % (n, ua[:60]))
        self._log("debug", "Cookie 名单: %s" % names)

        if self.export_cookie and cookie_dict:
            self.gate_cookie_blob = encode_cookie_blob(cookie_dict, ua)
            self._log("info", "已导出 Cookie 串（%d 字符），可存成 GitHub Secret %s"
                      % (len(self.gate_cookie_blob), COOKIE_ENV))
            print("\n===== 复制下面这一行，存成 GitHub Secret %s =====" % COOKIE_ENV)
            print(self.gate_cookie_blob)
            print("===== 结束 =====\n")

        try:
            resp = http.get(self.forum_url)
            if looks_like_forum(resp.text or ""):
                self._log("info", "HTTP 引擎复验通过（HTTP %s, %d 字节）"
                          % (resp.status_code, len(resp.text or "")))
                self.state = OPEN
                return True
            self._log("warning", "Cookie 已接管，但 HTTP 复验仍未通过（HTTP %s, %d 字节）"
                      % (resp.status_code, len(resp.text or "")))
        except Exception as exc:  # noqa: BLE001
            self._log("warning", "HTTP 复验异常: %r" % exc)
        return False

    # -- 打码平台兜底 -----------------------------------------------------

    def solve_with_capsolver(self, http, client_key):
        """
        可选兜底：用打码平台（CapSolver）直接取 Turnstile token，然后纯 HTTP 提交。
        站点校验接口 plugin.php?id=dev8133_cloudflare 本身不受门保护，所以这条路可行。
        """
        import urllib.request

        create = {
            "clientKey": client_key,
            "task": {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": self.forum_url,
                "websiteKey": TURNSTILE_SITEKEY,
            },
        }
        self._log("info", "向 CapSolver 提交 Turnstile 任务...")
        token = None
        task_id = None
        for _ in range(60):
            if task_id is None:
                req = urllib.request.Request(
                    "https://api.capsolver.com/createTask",
                    data=json.dumps(create).encode("utf-8"),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    res = json.loads(r.read().decode("utf-8"))
                if res.get("errorId"):
                    raise GateError("CapSolver 创建任务失败: %s" % res.get("errorDescription"))
                task_id = res.get("taskId")
                if not task_id:
                    time.sleep(5)
                    continue
                continue

            body = json.dumps({"clientKey": client_key, "taskId": task_id}).encode("utf-8")
            req2 = urllib.request.Request(
                "https://api.capsolver.com/getTaskResult", data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req2, timeout=30) as r:
                res2 = json.loads(r.read().decode("utf-8"))
            if res2.get("status") == "ready":
                sol = res2.get("solution") or {}
                token = sol.get("token") or sol.get("gRecaptchaResponse")
                if token:
                    break
            elif res2.get("errorId"):
                raise GateError("CapSolver 取结果失败: %s" % res2.get("errorDescription"))
            time.sleep(5)

        if not token:
            raise GateError("CapSolver 未在时限内返回 token")

        self._log("info", "已取得 Turnstile token（%d 字符），用 HTTP 提交站点校验接口" % len(token))
        if not self._submit_token(http, token):
            raise GateError("站点校验未通过（token 被拒）")
        self._log("info", "站点校验通过（code=200）")

        resp2 = http.get(self.forum_url)
        if looks_like_forum(resp2.text or ""):
            self.state = OPEN
            self._log("info", "HTTP 引擎复验通过（%d 字节）" % len(resp2.text or ""))
            if self.export_cookie:
                d = http.cookie_dict()
                if d:
                    self.gate_cookie_blob = encode_cookie_blob(d, CHROME_UA)
                    print("\n===== Cookie 串（存成 GitHub Secret %s）=====" % COOKIE_ENV)
                    print(self.gate_cookie_blob)
                    print("===== 结束 =====\n")
            return True
        raise GateError("校验通过但业务页仍被拦截（HTTP %s, %d 字节）"
                        % (resp2.status_code, len(resp2.text or "")))

    # -- 统一出口 ---------------------------------------------------------

    # -- 路线 A：用户自己的登录 Cookie（最稳，零浏览器）------------------

    @staticmethod
    def spider_ua():
        """取蜘蛛 UA。可用 GM_SPIDER_UA 覆盖；默认 Baiduspider（实测最稳）。"""
        custom = (os.getenv(SPIDER_UA_ENV) or "").strip()
        if custom:
            return custom
        return dict(SPIDER_UAS)["Baiduspider"]

    def try_user_cookie(self, http):
        """
        用 `GM_USER_COOKIE` 里的登录 Cookie + 蜘蛛 UA 直接开跑。

        为什么这条路最稳：
          * 蜘蛛 UA 让插件的门形同虚设（POST 也放行，只挡 misc.php）
          * 登录态由 Cookie 提供，**不需要验证码图片**（那正是被 403 的那个接口）
          * 不启动浏览器、不需要打码平台、不受出口 IP 信誉影响

        代价：Cookie 是你在自己浏览器里登录后复制出来的，30 天后要换一次。
        返回 True 表示可用。
        """
        raw = os.getenv(USER_COOKIE_ENV)
        if not raw:
            return False

        cookies = parse_cookie_header(raw)
        if not cookies:
            self._log("warning", "%s 解析后是空的，请检查粘贴内容" % USER_COOKIE_ENV)
            return False

        ua = self.spider_ua()
        # 失败时必须把 UA 还原，否则会把「借来的蜘蛛 UA」留给后面的 classify，
        # 让 classify 误判成「OPEN —— 普通 UA 可直连」（这个坑实测踩到过）。
        orig_ua = http.user_agent

        http.clear_cookies()
        http.set_user_agent(ua)
        http.set_cookies(cookies)
        http.set_referer(self.forum_url)
        self._log("info", "发现 %s（%d 项 Cookie），按「用户 Cookie 模式」直连"
                  % (USER_COOKIE_ENV, len(cookies)))
        self._log("debug", "Cookie 名单: %s" % ", ".join(sorted(cookies)))

        try:
            resp = http.get(self.url("/home.php?mod=spacecp"))
        except Exception as exc:  # noqa: BLE001
            self._log("warning", "用户 Cookie 验证请求异常: %r" % exc)
            http.clear_cookies()
            http.set_user_agent(orig_ua)
            return False

        body = resp.text or ""
        if is_gated(body):
            self._log("warning", "用户 Cookie 模式下仍被拦门（说明 GM_SPIDER_UA 不在白名单）")
            http.clear_cookies()
            http.set_user_agent(orig_ua)
            return False

        uid = detect_uid(body)
        if uid:
            self._log("info", "用户 Cookie 有效，已登录 uid=%d —— 跳过浏览器与验证码" % uid)
            self.state = OPEN
            self.gate_mode = USER_COOKIE_MODE
            self.logged_uid = uid
            return True   # 成功时保留蜘蛛 UA（后续请求都要用它）

        self._log("warning",
                  "用户 Cookie 已失效（服务端认为未登录）。"
                  "请重新在浏览器登录 %s 后复制新的 Cookie 串更新 %s"
                  % (self.hostname, USER_COOKIE_ENV))
        http.clear_cookies()
        http.set_user_agent(orig_ua)
        return False

    def try_preset_cookie(self, http):
        """
        先试 GM_GATE_COOKIE（CI 上可以完全跳过浏览器）。

        原理：过门后站点会种下 Cookie `TVj0_2132_cloudflare_check`（以及配套的 saltkey），
        带着它访问任何被门保护的页面都会直接放行。这个 Cookie 是长期有效的，
        所以「本地破门一次 → 把 Cookie 串存进 GitHub Secret → CI 直接复用」完全可行。
        """
        blob = os.getenv(COOKIE_ENV)
        if not blob:
            return False
        try:
            ua, cookies = decode_cookie_blob(blob)
        except Exception as exc:  # noqa: BLE001
            self._log("warning", "预置 Cookie 解析失败：%r" % exc)
            return False
        if not cookies:
            return False
        self._log("info", "发现预置 Cookie（%s），先试它" % COOKIE_ENV)
        http.clear_cookies()
        http.set_user_agent(ua)
        http.set_cookies(cookies)
        try:
            resp = http.get(self.forum_url)
            if looks_like_forum(resp.text or ""):
                self._log("info", "预置 Cookie 有效，跳过浏览器（HTTP %s, %d 字节）"
                          % (resp.status_code, len(resp.text or "")))
                self.state = OPEN
                return True
            self._log("warning", "预置 Cookie 已失效，改为现场破门")
        except Exception as exc:  # noqa: BLE001
            self._log("warning", "预置 Cookie 验证异常: %r" % exc)
        http.clear_cookies()
        return False

    def ensure_access(self, http):
        """
        保证 http 引擎可以正常访问站点。
        :return: 使用的方式（user_cookie / cookie / open / spider / browser / capsolver）
        """
        # 路线 A：用户自己的登录 Cookie（蜘蛛 UA，全程不碰 Turnstile）—— 最稳，优先
        if self.try_user_cookie(http):
            return USER_COOKIE_MODE

        # 路线 B：之前破门导出的放行 Cookie
        if self.try_preset_cookie(http):
            return "cookie"

        state = self.classify(http)
        if state == OPEN:
            return OPEN

        solver = (os.getenv("GM_SOLVER") or "").strip().lower()
        cap_key = os.getenv("CAPSOLVER_KEY")
        if solver == "capsolver" or (solver == "" and cap_key
                                     and os.getenv("GM_PREFER_CAPSOLVER")):
            self._log("info", "使用打码平台（CapSolver）破门")
            try:
                if self.solve_with_capsolver(http, cap_key):
                    return "capsolver"
            except Exception as exc:  # noqa: BLE001
                self._log("error", "打码平台破门失败: %r" % exc)

        try:
            if self.solve_with_browser(http):
                return "browser"
        except GateError as exc:
            self._log("error", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._log("error", "浏览器破门异常: %r" % exc)

        # 浏览器失败后，若配了打码平台再兜一次
        if cap_key and solver not in ("capsolver", "browser"):
            try:
                self._log("warning", "浏览器破门失败，改用打码平台兜底")
                if self.solve_with_capsolver(http, cap_key):
                    return "capsolver"
            except Exception as exc:  # noqa: BLE001
                self._log("error", "打码平台兜底也失败: %r" % exc)

        # 最后实在不行，降级为爬虫白名单（只能读，登录必失败）
        if state == SPIDER_OK or self.state == SPIDER_OK:
            self._log("warning", "破门失败，降级为爬虫白名单 UA（只能读公开页面，无法登录）")
            spider_ua = dict(SPIDER_UAS).get(self.spider_ua_name or "Baiduspider")
            if spider_ua:
                http.set_user_agent(spider_ua)
                return SPIDER_OK

        raise GateError("无法通过站点验证门，本次任务中止")

    # -- 登录链路自检（不需要账号密码）-----------------------------------

    def probe_login(self, http):
        """
        过门之后，验证「登录页 + 验证码图片」这条链路在纯 HTTP 下是否真的通。

        关键点（实测）：登录页里**没有** name="seccodehash" 的隐藏域，
        真正的 idhash 藏在 JavaScript 调用里：
            updateseccode('cSBPIt5f', '<div class="rfm">...', 'member::logging')
        原项目写死 idhash=cSA —— 所以每次取验证码都 403。这里必须动态解析。
        """
        result = {"login_page": False, "idhash": None, "seccode_image": False,
                  "image_bytes": 0, "ocr": None, "note": ""}
        login_url = "https://%s/member.php?mod=logging&action=login" % self.hostname
        ref = "https://%s/" % self.hostname

        try:
            r = http.get(login_url, headers={"Referer": ref})
        except Exception as exc:  # noqa: BLE001
            result["note"] = "请求登录页异常: %r" % exc
            return result
        html = r.text or ""
        if is_gated(html):
            result["note"] = "登录页仍被验证门拦住"
            return result
        result["login_page"] = True

        m = re.search(r"updateseccode\('([A-Za-z0-9]+)'", html)
        if not m:
            m = re.search(r'id="seccode_([A-Za-z0-9]+)"', html)
        if not m:
            m = re.search(r'name="seccodehash"[^>]*value="([^"]+)"', html)
        if not m:
            result["note"] = "登录页里找不到 seccode idhash（可能本次不需要验证码）"
            return result
        idhash = m.group(1)
        result["idhash"] = idhash

        img_url = ("https://%s/misc.php?mod=seccode&update=%d&idhash=%s&modid=member::logging"
                   % (self.hostname, int(time.time() * 1000) % 1000000, idhash))
        try:
            r2 = http.get(img_url, headers={
                "Referer": login_url,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"})
        except Exception as exc:  # noqa: BLE001
            result["note"] = "请求验证码图片异常: %r" % exc
            return result

        ct = r2.headers.get("Content-Type", "")
        if r2.status_code == 200 and "image" in ct.lower() and r2.content:
            result["seccode_image"] = True
            result["image_bytes"] = len(r2.content)
            try:
                import ddddocr
                ocr = ddddocr.DdddOcr(show_ad=False)
                result["ocr"] = ocr.classification(r2.content)
            except Exception as exc:  # noqa: BLE001
                result["note"] = "验证码图片已拿到，但 ddddocr 不可用: %r" % exc
        else:
            result["note"] = "验证码图片拿不到：HTTP %s / %d 字节 / %s" % (
                r2.status_code, len(r2.content), ct)
        return result

    def close(self):
        if self.page is not None:
            try:
                self.page.quit()
                self._log("info", "浏览器已关闭")
            except Exception:  # noqa: BLE001
                pass
            self.page = None


# ---------------------------------------------------------------- 带门保护的会话


class GatedSession:
    """
    包一层：每次请求后自动检查是否又被门拦住；若是，则重新破门并重放该请求。
    这样即使站点在会话中途让验证过期，业务也不会无声失败。
    """

    def __init__(self, http, gate, logger=None, max_resolve=2):
        self.http = http
        self.gate = gate
        self.logger = logger
        self.max_resolve = max_resolve

    def _log(self, level, msg):
        if self.logger:
            getattr(self.logger, level)(msg)

    def _request(self, method, url, **kw):
        attempt = 0
        while True:
            resp = getattr(self.http, method)(url, **kw)
            text = resp.text or ""
            if not is_gated(text):
                return resp
            attempt += 1
            if attempt > self.max_resolve:
                self._log("error", "请求 %s 反复被验证门拦截，放弃" % url)
                return resp
            self._log("warning", "请求被验证门拦截，重新破门后重试（第 %d 次）: %s"
                      % (attempt, url))
            self.gate.ensure_access(self.http)
            self.http.set_referer("https://%s/" % self.http.hostname)

    def get(self, url, **kw):
        return self._request("get", url, **kw)

    def post(self, url, **kw):
        return self._request("post", url, **kw)


# ---------------------------------------------------------------- 命令行诊断


def _build_logger(verbose=False):
    import logging
    logger = logging.getLogger("GateProbe")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s",
                                         datefmt="%H:%M:%S"))
        logger.addHandler(h)
    return logger


def main():
    parser = argparse.ArgumentParser(description="GameMale 验证门诊断工具")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--solve", action="store_true", help="实际尝试浏览器破门")
    parser.add_argument("--headed", action="store_true", help="用有头浏览器（推荐）")
    parser.add_argument("--headless", action="store_true", help="强制无头浏览器（基本过不了）")
    parser.add_argument("--manual", action="store_true", help="人工模式：手动点验证（本地救急）")
    parser.add_argument("--export", action="store_true", help="破门后导出 Cookie 串")
    parser.add_argument("--probe-login", action="store_true",
                        help="破门后验证登录页 + 验证码图片是否可达")
    parser.add_argument("--capsolver", default=None, help="直接用 CapSolver key 破门")
    parser.add_argument("--chrome", default=None, help="Chrome 可执行文件路径")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logger = _build_logger(args.verbose)
    http = HttpEngine(args.host, logger)
    logger.info("HTTP 引擎: %s" % http.kind)

    gate = GateKeeper(args.host, logger, browser_path=args.chrome,
                      manual=args.manual, export_cookie=args.export)
    if args.headless:
        gate.headless = True
    if args.headed:
        gate.headless = False

    how = None

    # 顺序与 ensure_access() 完全一致：用户 Cookie -> 过门 Cookie -> 现场破门
    # 路线 A：用户自己的登录 Cookie（命中就完全不必碰 Turnstile）
    if gate.try_user_cookie(http):
        print("\n[门口状态] 路线A 用户 Cookie —— 已是登录态（uid=%s），跳过 Turnstile 与验证码"
              % gate.logged_uid)
        how = "用户 Cookie（%s）" % USER_COOKIE_ENV
    elif gate.try_preset_cookie(http):
        print("\n[门口状态] 路线A' 过门 Cookie 有效（%s）" % COOKIE_ENV)
        how = "预置 Cookie（%s）" % COOKIE_ENV

    if how:
        state = OPEN
    else:
        state = gate.classify(http)
        print("\n[门口状态] %s" % state)

    if how:
        pass
    elif args.capsolver:
        try:
            ok = gate.solve_with_capsolver(http, args.capsolver)
            how = "CapSolver" if ok else None
            print("[CapSolver 破门] %s" % ("成功" if ok else "失败"))
        except GateError as exc:
            print("[CapSolver 破门失败] %s" % exc)
    elif args.solve:
        try:
            ok = gate.solve_with_browser(http)
            how = "浏览器" if ok else None
            print("[浏览器破门] %s" % ("成功" if ok else "失败"))
        except GateError as exc:
            print("[浏览器破门失败] %s" % exc)
    else:
        print("[提示] 未指定 --solve / --capsolver，只做探测。"
              "若已配置 %s / %s 会自动复用。"
              % (USER_COOKIE_ENV, COOKIE_ENV))

    if how:
        print("[过门方式] %s" % how)

    # 无论走哪条路，最后都复核一次
    try:
        r = http.get(gate.forum_url)
        print("[复验] HTTP %s / %d 字节 / 像论坛页=%s"
              % (r.status_code, len(r.text or ""), looks_like_forum(r.text or "")))
    except Exception as exc:  # noqa: BLE001
        print("[复验] 异常 %r" % exc)

    if args.probe_login:
        info = gate.probe_login(http)
        print("\n[登录链路自检]")
        print("  登录页可达      : %s" % info["login_page"])
        print("  解析到 idhash   : %s" % info["idhash"])
        print("  验证码图片可取  : %s（%d 字节）" % (info["seccode_image"], info["image_bytes"]))
        print("  OCR 结果        : %s" % info["ocr"])
        if info["note"]:
            print("  备注            : %s" % info["note"])

    gate.close()


if __name__ == "__main__":
    main()
