# -*- coding: utf-8 -*-
"""
GM-All-In-One v1.1 —— GameMale 论坛签到一条龙

与上游原版的差异（修复纲要）
================================================================
[P0-1] 验证门识别错误：原版等的是 Cloudflare 的 "Just a moment" 标题，而站点实际返回的是
       自带插件 dev8133_cloudflare 的「请稍候...」Turnstile 门。现在按门标记
       (dev8133_cloudflare) 判定并轮询等待，不再固定 sleep(8)。
[P0-2] 浏览器进程托管不可靠：原版让 CI 在独立 step 里 `&` 起 Chrome，进程可能被回收。
       现在由脚本自己启动/关闭浏览器，本地与 CI 行为一致。
[P0-3] 依赖未锁版本：新增 requirements.txt。
[P0-4] 会话中途被重新拦门时无补救：新增 GatedSession，业务请求被拦门会自动重新破门并重放。
[P0-5] DrissionPage 4.x 的 cookies() 不接受 as_dict 关键字（原版写法会抛 TypeError），
       且未显式指定浏览器路径时会 BrowserConnectError —— 均已在 gm_gate.py 修正。
[P1-2] idhash=cSA 硬编码 → 改为从登录页解析 seccodehash / seccodemodid。
[P1-3] 一天跑两次都是全量重复 → 支持 GM_RUN_MODE=light（只登录 + 抓资产 + 发报告）。
[P1-4] 资产抓取失败时会写入 0 污染金币基准 → 现在仅在解析成功时才回写 gold_record.txt。
[P1-5] 裸 except: pass 掩盖故障 → 全部改为带日志的捕获。
[P1-6] 安全提问未接入 → 支持 GM_QUESTIONID / GM_ANSWER 环境变量。
[P1-7] 互动对象 UID 硬编码 → 支持 GM_UIDS 覆盖。
[P2-3] 验证码 update 地址缺参数名（&0.1234567）→ 改为 &_=<时间戳>。
[P2-6] 日志表态计数初值 1 导致报告失真 → 改为 0。
[新增] 运行失败也会发一封说明邮件（原版失败时静默）。
[新增] --check 自检模式：只破门 + 登录 + 抓资产，不发邮件，用于首次部署验证。

[新增·第二期] 过门路线重排（理由见 gm_gate.py 顶部）
    路线 A  GM_USER_COOKIE（推荐）：蜘蛛 UA + 你自己的登录 Cookie，
            **完全不碰 Turnstile、不需要验证码、不需要浏览器**。
    路线 B  CAPSOLVER_KEY：打码平台解 Turnstile → 提交换放行 Cookie → HTTP 登录（验证码走 ddddocr）。
    路线 C  浏览器破门（xvfb + 有头 Chrome）：兜底。实测受出口 IP 信誉影响很大。

用法
----
    GM_USERNAME / GM_PASSWORD                                必需（路线 A 下仅用于兜底登录）
    GM_USER_COOKIE                                           强烈推荐（见下方说明）
    GM_SMTP_HOST / GM_MAIL_USER / GM_MAIL_PASS / GM_MAIL_TO   可选，缺省则不发邮件
    CAPSOLVER_KEY                                            可选，打码平台兜底
    （兼容上游旧变量名 USERNAME / PASSWORD / SMTP_HOST / MAIL_USER / MAIL_PASS / MAIL_TO；
      注意 Windows 上 USERNAME 是系统内置变量，本地务必用 GM_USERNAME）

    关于 GM_USER_COOKIE —— 怎么拿？
      在自己电脑的浏览器里正常登录 www.gamemale.com，然后：
        F12 → 网络(Network) → 随便点一个 gamemale.com 的请求
        → 请求头(Request Headers) → 复制整行 Cookie: 后面的值
      （也可以在 应用/Application → Cookies → https://www.gamemale.com 里
        找 TVj0_2132_auth、TVj0_2132_saltkey、TVj0_2132_sid 拼成 name=value; name=value）
      粘贴时必须带上，否则服务端认不出登录态。
      有效期约 30 天（登录时 cookietime=2592000），过期后重新复制一次即可。

    python gamemale.py            正常运行
    python gamemale.py --check    自检：破门 + 登录 + 抓资产，不发邮件

更新记录
--------
v1.1（第一次更新，2026-09-23）
  1. 邮件报告恢复 emoji 展示，署名改为「GM-All-In-One v1.1」。
  2. 资产对比从「只有金币」扩展为「金币 + 血液」都比对上次；首次运行不会报错。
  3. 新增积分与等级展示（🏅 积分 / 🎖️ Lvl. N）。
  4. 新增「还要献祭多少血液才能升级」的估算（1 积分 ≈ 34 血液）。
  5. 基准文件 gold_record.txt（纯数字）→ asset_record.json（可存多项，带日期），
     旧文件仍可读取，首次迁移不会丢基准。
  6. 基准文件带「账号指纹」归属标识：别人克隆/复刻本仓库后，第一次运行不会把
     你的金币/血液/积分当成他的基准（否则会显示一个虚假的大额涨跌）。
     空文件/损坏文件不再连带跳过旧 gold_record.txt 的兼容读取。
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import re
import smtplib
import sys
import time
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from gm_gate import (
    DEFAULT_HOST,
    SPIDER_BLOCKED_PATHS,
    USER_COOKIE_ENV,
    USER_COOKIE_MODE,
    GateError,
    GateKeeper,
    GatedSession,
    HttpEngine,
    is_gated,
)

try:
    import ddddocr
except Exception as _exc:  # noqa: BLE001
    ddddocr = None
    _DDDDOCR_IMPORT_ERROR = _exc
else:
    _DDDDOCR_IMPORT_ERROR = None

DEFAULT_UIDS = [730713, 62445, 61832]
# 「积分」在积分页不一定出现，抓不到时会自动补抓一次空间首页（见 fetch_assets）
ASSET_ITEMS = ["金币", "血液", "旅程", "追随", "知识", "咒术", "堕落", "灵魂", "积分"]
# 需要做「较上次」对比的项
TRACKED_ITEMS = ["金币", "血液", "积分"]

VERSION = "v1.1"

# ---- 等级门槛（下标即为等级号）----
# 用户提供：lv0-0 lv1-3 lv2-10 lv3-35 lv4-? lv5-120 lv6-200 lv7-300 lv8-450 lv9-650 lv10-900
# 原稿 lv4 写作 14，与「门槛单调递增」矛盾（14 < 35），此处按序列取 70。
# 它只影响「积分落在 35~120 之间时显示的当前等级」；若与站内实际不符，直接改这一行即可。
LEVEL_THRESHOLDS = [0, 3, 10, 35, 70, 120, 200, 300, 450, 650, 900]

# ---- 献祭换算 ----
# 献祭血液 → 增加旅程，1 点旅程 = 1 点积分。原始税率 15%（33.5 血液 = 1 旅程），
# 按实测「暴力」口径取整为 34 血液 = 1 积分。
BLOOD_PER_POINT = 34

# 资产基准文件：v1.1 起用 JSON（可同时记录金币/血液/积分），旧的纯数字文件仍兼容读取
ASSET_RECORD_JSON = "asset_record.json"
ASSET_RECORD_LEGACY = "gold_record.txt"

# ---- 基准文件「归属标识」----
# 基准文件是要提交进仓库的（否则 Actions 每次都是全新环境，没法对比昨天）。
# 于是有人克隆/复刻这个仓库后，第一次运行会读到**别人的**基准，报告里就会出现
# 一个莫名其妙的大额涨跌（比如「金币 200（较上次 -1549）」）。
# 解决：写入时附带一组当前账号的**指纹**，读取时对不上就整份忽略、按「首次记录」处理。
# 只存哈希、不含 uid / 用户名明文 —— 但盐是公开的，所以它只负责「归属比对」，不承担保密职责。
ASSET_OWNER_SALT = "GM-All-In-One/asset-record/v1"
# 这些是占位值，不能当身份来源（否则不同用户会撞成同一个指纹）
OWNER_PLACEHOLDER_NAMES = {
    "placeholder", "test", "username", "your_username", "yourname", "your_name",
    "example", "none", "null", "changeme", "gm_username", "account", "user",
}
UID_RE = re.compile(r"discuz_uid\s*=\s*['\"]?(\d+)")
UID_HOME_RE = re.compile(r"home\.php\?mod=space&(?:amp;)?uid=(\d+)")


# =============================================================== 工具函数


def setup_logger(name, verbose=False):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-10s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def env(*names, **kw):
    """按顺序取第一个非空环境变量。"""
    default = kw.get("default")
    for n in names:
        v = os.getenv(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default


def parse_uids(raw):
    if not raw:
        return list(DEFAULT_UIDS)
    out = []
    for part in re.split(r"[,\s;]+", raw.strip()):
        if part.isdigit():
            out.append(int(part))
    return out or list(DEFAULT_UIDS)


# ------------------------------------------------ v1.1 新增：积分 / 等级 / 基准


def level_of(points):
    """按 LEVEL_THRESHOLDS 返回 (当前等级号, 本级门槛, 下一级门槛或 None)。"""
    lv = 0
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if points >= threshold:
            lv = i
        else:
            break
    nxt = LEVEL_THRESHOLDS[lv + 1] if lv + 1 < len(LEVEL_THRESHOLDS) else None
    return lv, LEVEL_THRESHOLDS[lv], nxt


def fmt_growth(cur, prev):
    """生成「（较上次 +N）」后缀。首次记录没有基准时返回「（首次记录）」而不是报错。"""
    if cur is None:
        return ""
    if prev is None:
        return " （首次记录）"
    delta = cur - prev
    return " （较上次 +%d）" % delta if delta >= 0 else " （较上次 %d）" % delta


def asset_owner_fp(kind, value):
    """把身份压成不可逆短指纹（只用于比对，不能反推出 uid / 用户名）。"""
    raw = ("%s|%s|%s" % (ASSET_OWNER_SALT, kind, value)).encode("utf-8")
    return "%s:%s" % (kind, hashlib.sha1(raw).hexdigest()[:10])


def build_asset_owner(uid=None, username=None):
    """生成本次运行的归属指纹集合。取不到任何身份就返回空列表。"""
    ids = set()
    if uid:
        ids.add(asset_owner_fp("uid", str(uid)))
    name = str(username or "").strip()
    if name and name.lower() not in OWNER_PLACEHOLDER_NAMES:
        ids.add(asset_owner_fp("user", name.lower()))
    return sorted(ids)


def asset_owner_matches(stored, current):
    """
    基准文件是不是本账号的？

    信息不足时（文件里没记 / 这次算不出来）一律放行 —— 宁可少拦一次，
    也不能让正常用户因为某次读不到 uid 就被反复重置基准。
    两边都有信息时取交集：任一项对上就算自己（用户改名、换登录方式都不会误伤）。
    """
    if not stored or not current:
        return True
    if isinstance(stored, str):
        stored = [stored]
    try:
        return bool(set(stored) & set(current))
    except TypeError:
        return True


def detect_uid_in_html(html):
    """从页面里读 discuz_uid。游客(0)不算命中，读不到返回 None。"""
    for pattern in (UID_RE, UID_HOME_RE):
        m = pattern.search(html or "")
        if not m:
            continue
        try:
            uid = int(m.group(1))
        except ValueError:
            continue
        if uid > 0:
            return uid
    return None


def load_asset_record(logger=None, owner_ids=None):
    """
    读取上次的资产基准。以下三种情况都按「首次记录」处理，**绝不抛错**：

      1. 文件不存在 / 是空的 / 内容损坏 / 类型不对；
      2. 文件属于**别的账号**（克隆、复刻别人仓库后第一次运行的常见情况）——
         否则会把别人的金币/血液当成自己的基准，报告里冒出一个大额虚假涨跌；
      3. 只有 v1.0 的旧 gold_record.txt（迁移期只取金币）。

    注意 1 和 3 的关系：JSON 只是「空的/坏的」时，仍要继续尝试旧的 gold_record.txt。
    早先用 if/elif 写会把这两条互斥掉 —— 一个 0 字节的 JSON 就能让老用户的
    金币基准凭空消失，所以这里改成了「先试 JSON，不可用了再试旧文件」。
    """
    rec, json_usable = {}, False

    if os.path.exists(ASSET_RECORD_JSON):
        data = None
        try:
            with open(ASSET_RECORD_JSON, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            if logger:
                logger.warning("读取 %s 失败（按首次记录处理）: %r" % (ASSET_RECORD_JSON, exc))

        if isinstance(data, dict) and data:
            if asset_owner_matches(data.get("_owner"), owner_ids):
                rec, json_usable = data, True
            else:
                # 归属不符：连旧的 gold_record.txt 一起忽略（同一个仓库里的都是别人的）
                if logger:
                    logger.info("基准文件 %s 记录的是其它账号（克隆/复刻仓库时常见），"
                                "本次全部按「首次记录」处理，运行结束会自动改写成本账号的基准"
                                % ASSET_RECORD_JSON)
                return {}
        elif data is not None and logger:
            logger.warning("%s 内容不是有效基准（按首次记录处理）" % ASSET_RECORD_JSON)

    if not json_usable and os.path.exists(ASSET_RECORD_LEGACY):
        # 兼容 v1.0 的纯数字 gold_record.txt（迁移期不丢金币基准）
        try:
            with open(ASSET_RECORD_LEGACY, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
            if content.isdigit():
                rec["金币"] = int(content)
        except Exception as exc:  # noqa: BLE001
            if logger:
                logger.warning("读取 %s 失败: %r" % (ASSET_RECORD_LEGACY, exc))
    return rec


def save_asset_record(rec, logger=None):
    """写入基准。失败只告警不抛错（下次会重新按首次记录处理）。"""
    try:
        with open(ASSET_RECORD_JSON, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.warning("写入 %s 失败: %r" % (ASSET_RECORD_JSON, exc))
        return False


# =============================================================== 主体


class Gamemale:

    def __init__(self, username, password, questionid="0", answer=None,
                 verbose=False, hostname=DEFAULT_HOST, run_mode="full",
                 uids=None, chrome_path=None):
        self.verbose = verbose
        self.hostname = hostname
        self.run_mode = (run_mode or "full").lower()
        self.uids = uids or list(DEFAULT_UIDS)

        self.main_logger = setup_logger("GameMale", verbose)
        self.login_logger = setup_logger("登录", verbose)
        self.sign_logger = setup_logger("签到", verbose)
        self.exchange_logger = setup_logger("抽奖", verbose)
        self.task_logger = setup_logger("日常任务", verbose)
        self.notice_logger = setup_logger("通知", verbose)

        self.username = str(username)
        self.password = str(password)
        self.questionid = str(questionid or "0")
        self.answer = str(answer or "")

        self.post_formhash = None
        self.logged_in = False
        self.gate_mode = "unknown"
        self.fatal_error = None
        self.spider_mode = False   # 蜘蛛 UA + 用户 Cookie：misc.php 不可用，登录流程整体跳过
        self.my_uid = None         # 本账号 uid（页面里读到就记下），用于基准文件归属校验

        self.sign_result = "未执行"
        self.exchange_result = "未执行"
        self.task_result = "未执行"
        self.assets_report = "未抓取"
        self.assets_ok = False

        # HTTP 引擎（优先 curl_cffi，抗 TLS 指纹识别）
        self.http = HttpEngine(hostname, self.main_logger)
        self.main_logger.info("HTTP 引擎: %s" % self.http.kind)

        # 验证门处理
        self.gate = GateKeeper(hostname, self.main_logger, browser_path=chrome_path)
        self.sess = GatedSession(self.http, self.gate, self.main_logger)

        # 验证码识别
        self.ocr = None
        if ddddocr is None:
            self.login_logger.warning("ddddocr 不可用（%s），若站点要求验证码将无法登录"
                                      % _DDDDOCR_IMPORT_ERROR)
        else:
            try:
                self.ocr = ddddocr.DdddOcr(show_ad=False)
            except Exception as exc:  # noqa: BLE001
                self.login_logger.warning("ddddocr 初始化失败: %r" % exc)

    # ---------------------------------------------------------- 基础

    def _url(self, path):
        return "https://%s%s" % (self.hostname, path)

    def refresh_formhash(self):
        """从论坛页面提取全局 formhash（所有写操作都要带）。"""
        text = self.sess.get(self._url("/forum.php")).text or ""
        m = re.search(r'<input type="hidden" name="formhash" value="(.+?)"', text)
        if m:
            self.post_formhash = m.group(1)
            return self.post_formhash
        return None

    # ---------------------------------------------------------- 过门

    def connect(self):
        """接管站点访问（必要时用浏览器过 Turnstile 验证门）。"""
        self.main_logger.info("=== 检查站点验证门 ===")
        try:
            self.gate_mode = self.gate.ensure_access(self.http)
        except GateError as exc:
            self.fatal_error = "无法通过站点验证门: %s" % exc
            self.main_logger.error(self.fatal_error)
            return False
        self.main_logger.info("验证门处理完成，模式: %s" % self.gate_mode)
        self.http.set_referer(self._url("/forum.php"))

        if self.gate_mode == USER_COOKIE_MODE:
            # 路线 A：Cookie 已经带着登录态，后面直接跳过登录
            self.logged_in = True
            self.spider_mode = True
            self.http.set_referer(self._url("/forum.php"))
            if not self.refresh_formhash():
                self.main_logger.warning("用户 Cookie 模式下未取到 formhash，写操作可能失败")
            return True

        if self.gate_mode == "spider":
            # 爬虫白名单能读能写，但 misc.php 整个文件 403 —— 所以拿不到登录验证码。
            # 没有登录 Cookie 时这里就是死路，必须点明原因。
            self.fatal_error = (
                "站点验证门只放行了爬虫 UA，登录验证码接口 misc.php 对爬虫 UA 返回 403，"
                "无法完成登录。\n"
                "  两条出路：\n"
                "    1) 推荐：设置 %s（你自己浏览器的登录 Cookie），全程不必碰 Turnstile；\n"
                "    2) 或者排查浏览器破门为何没生效（GM_HEADLESS / xvfb / GM_CHROME_PATH），"
                "或配置 CAPSOLVER_KEY 走打码平台。" % USER_COOKIE_ENV)
            self.main_logger.error(self.fatal_error)
            return False
        return True

    # ---------------------------------------------------------- 登录

    @staticmethod
    def _extract_login_fields(html):
        """
        解析登录页表单要素。

        ⚠️ 关键实测结论：本站登录页**没有** name="seccodehash" 的隐藏域，
        真正的 idhash 藏在页面底部的 JavaScript 调用里：

            <span id="seccode_cSBPIt5f"></span>
            <script>updateseccode('cSBPIt5f', '<div class="rfm">…', 'member::logging');</script>

        那对 input 是 updateseccode() 运行时才注入的。
        原项目写死 idhash=cSA，所以取验证码图片永远 403（站点返回空 body 的 403）。
        这里必须按优先级动态解析。
        """
        html = html or ""
        out = {"loginhash": None, "formhash": None,
               "seccodehash": None, "seccodemodid": None,
               "has_seccode_input": False}

        m = re.search(r'<div id="main_messaqge_(.+?)">', html)
        if m:
            out["loginhash"] = m.group(1)
        m = re.search(r'name="formhash"\s+value="(.+?)"', html)
        if m:
            out["formhash"] = m.group(1)

        # idhash 解析：三条路依次尝试
        for pattern in (
            r"updateseccode\('([A-Za-z0-9]+)'",            # 本站的真实形态
            r'id="seccode_([A-Za-z0-9]+)"',
            r'name="seccodehash"[^>]*value="([^"]+)"',
        ):
            m = re.search(pattern, html)
            if m:
                out["seccodehash"] = m.group(1)
                break

        m = re.search(r"updateseccode\('[A-Za-z0-9]+',\s*'[^']*',\s*'([^']+)'", html)
        if not m:
            m = re.search(r'name="seccodemodid"[^>]*value="([^"]+)"', html)
        out["seccodemodid"] = m.group(1) if m else "member::logging"

        # 有 idhash 就说明这次登录要过验证码
        out["has_seccode_input"] = bool(out["seccodehash"]) or ("seccodeverify" in html)
        return out

    def _fetch_seccode(self, seccodehash, modid, max_retries=8):
        """
        拉取验证码图片并 OCR。

        流程（对齐 Discuz 的 updateseccode() 实现）：
          1) GET misc.php?mod=seccode&action=update&idhash=X&modid=Y  → 返回含
             <img src="misc.php?mod=seccode&update=NNNN&idhash=X&modid=Y"> 的 HTML
          2) 从里面抠出 update=NNNN，再 GET 图片
          3) 拿到图 → ddddocr 识别
          4) GET ...&action=check... 让服务端先校验一次，通过再拿去 POST 登录
        第 2 步取不到 update 时，退回「随机 update 直取图片」。
        """
        if self.ocr is None:
            return ""
        modid = modid or "member::logging"
        if self.spider_mode:
            # 蜘蛛 UA 下 misc.php 整个文件被插件 403，取图必失败，直接点明不要白试 8 轮
            self.login_logger.error(
                "当前是蜘蛛 UA 模式，misc.php 被站点 403 封死，无法获取验证码图片。"
                "请改用 GM_USER_COOKIE（自带登录态，无需验证码）")
            return ""
        if not seccodehash:
            self.login_logger.error("没有 idhash，无法取验证码")
            return ""
        self.login_logger.info("开始识别验证码（idhash=%s, 最多 %d 次）" % (seccodehash, max_retries))
        referer = self._url("/member.php?mod=logging&action=login")

        for attempt in range(1, max_retries + 1):
            try:
                img = b""

                # --- 第 1 步：问服务端要一次 update 串 -------------------
                update_url = self._url(
                    "/misc.php?mod=seccode&action=update&idhash=%s&modid=%s&_=%d"
                    % (seccodehash, modid, int(time.time() * 1000)))
                try:
                    update_text = self.sess.get(
                        update_url, headers={"Referer": referer}).text or ""
                except Exception:  # noqa: BLE001
                    update_text = ""
                m = re.search(r"update=(\w+)&idhash=", update_text)

                # --- 第 2 步：取图片 --------------------------------------
                if m:
                    code_url = self._url(
                        "/misc.php?mod=seccode&update=%s&idhash=%s&modid=%s"
                        % (m.group(1), seccodehash, modid))
                else:
                    code_url = self._url(
                        "/misc.php?mod=seccode&update=%d&idhash=%s&modid=%s"
                        % (int(time.time() * 1000) % 1000000, seccodehash, modid))

                resp = self.sess.get(code_url, headers={
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": referer,
                })
                ct = (resp.headers.get("Content-Type") or "").lower()
                if "image" in ct:
                    img = resp.content or b""
                if not img:
                    self.login_logger.debug(
                        "第 %d 次验证码图片为空（HTTP %s, %s）"
                        % (attempt, resp.status_code, ct or "无 Content-Type"))
                    time.sleep(0.5)
                    continue

                code = self.ocr.classification(img)
                if not code:
                    continue

                check_url = self._url(
                    "/misc.php?mod=seccode&action=check&inajax=1&modid=%s&idhash=%s&secverify=%s"
                    % (modid, seccodehash, code))
                if "succeed" in (self.sess.get(check_url, headers={
                        "X-Requested-With": "XMLHttpRequest"}).text or ""):
                    self.login_logger.info("验证码识别成功: %s（第 %d 次）" % (code, attempt))
                    return code
                self.login_logger.debug("第 %d 次识别结果 '%s' 未通过" % (attempt, code))
            except Exception as exc:  # noqa: BLE001
                self.login_logger.warning("验证码流程异常（第 %d 次）: %r" % (attempt, exc))
            time.sleep(0.5)
        self.login_logger.error("验证码识别失败")
        return ""

    def login(self):
        self.login_logger.info("开始登录流程...")
        login_page = self._url("/member.php?mod=logging&action=login")

        # 密码有两种提交形态，必须都试：
        #   Discuz 登录表单的 onsubmit 会调 pwmd5()，把明文密码就地换成 md5 再提交；
        #   但服务端同时也接受明文（历史兼容）。所以第 1/3 轮用 md5，第 2 轮用明文。
        pw_md5 = hashlib.md5(self.password.encode("utf-8")).hexdigest()
        # loginfield 是「按哪个字段登录」（username / email），不是用户名本身。
        # 上游原版填的是用户名，恰好被 Discuz 当成非 email 兜底成 username 才没炸。
        login_field = (os.getenv("GM_LOGIN_FIELD") or "username").strip() or "username"

        for round_no in range(1, 4):
            html = self.sess.get(login_page).text or ""
            f = self._extract_login_fields(html)
            if not f["loginhash"] or not f["formhash"]:
                self.login_logger.error(
                    "第 %d 轮：登录页缺少 loginhash/formhash（%d 字节，仍在验证门内=%s）"
                    % (round_no, len(html), is_gated(html)))
                time.sleep(2)
                continue

            seccodehash = f["seccodehash"] or ""
            modid = f["seccodemodid"] or "member::logging"
            code = ""
            if seccodehash:
                code = self._fetch_seccode(seccodehash, modid)
            else:
                self.login_logger.warning("登录页未出现验证码 idhash，本次尝试不带验证码")

            password_to_send = pw_md5 if round_no != 2 else self.password
            login_url = self._url(
                "/member.php?mod=logging&action=login&loginsubmit=yes&loginhash=%s&inajax=1"
                % f["loginhash"])
            form = {
                "formhash": f["formhash"],
                "referer": self._url("/"),
                "loginfield": login_field,
                "username": self.username,
                "password": password_to_send,
                "questionid": self.questionid,
                "answer": self.answer,
                "cookietime": 2592000,
                "seccodehash": seccodehash,
                "seccodemodid": modid,
                "seccodeverify": code,
            }
            try:
                resp_text = self.sess.post(login_url, data=form, headers={
                    "Referer": login_page,
                    "Origin": "https://%s" % self.hostname,
                    "Content-Type": "application/x-www-form-urlencoded",
                }).text or ""
            except Exception as exc:  # noqa: BLE001
                self.login_logger.error("登录请求异常（第 %d 轮）: %r" % (round_no, exc))
                time.sleep(2)
                continue

            if "succeed" in resp_text:
                self.logged_in = True
                self.login_logger.info(
                    "登录成功（第 %d 轮，密码形态=%s）"
                    % (round_no, "md5" if round_no != 2 else "明文"))
                if self.refresh_formhash():
                    self.login_logger.info("已获取全局 formhash")
                else:
                    self.login_logger.warning("未能获取全局 formhash，后续写操作可能失败")
                return True

            snippet = re.sub(r"<[^>]+>", "", resp_text).strip()[:120]
            self.login_logger.warning("第 %d 轮登录未成功: %s" % (round_no, snippet or "(空响应)"))

            # 只有"验证码"类错误才值得换一张验证码重来
            if "验证码" not in resp_text and "seccode" not in resp_text.lower():
                # 非验证码错误：如果这轮用的是明文密码，说明密码形态也错了，继续换形态再试一次
                if round_no == 2:
                    self.login_logger.error(
                        "非验证码原因失败，请检查账号密码 / 安全提问配置")
                    break
                self.login_logger.info("换一种密码提交形态再试一轮")
                continue
            time.sleep(1)

        self.login_logger.error("登录失败，请检查凭证或安全提问设置")
        return False

    # ---------------------------------------------------------- 每日签到

    def sign_gamemale(self):
        self.sign_logger.info("执行每日签到...")
        if not self.post_formhash:
            self.sign_result = "失败：缺少 formhash"
            self.sign_logger.error(self.sign_result)
            return
        url = self._url("/k_misign-sign.html?operation=qiandao&format=button&formhash=%s"
                        % self.post_formhash)
        try:
            res = self.sess.get(url).text or ""
            if "签到成功" in res:
                self.sign_result = "签到成功"
            elif "已签" in res:
                self.sign_result = "今日已签到"
            elif is_gated(res):
                self.sign_result = "失败：被验证门拦截"
            else:
                self.sign_result = "未知响应状态"
            self.sign_logger.info("签到结果: %s" % self.sign_result)
        except Exception as exc:  # noqa: BLE001
            self.sign_result = "异常: %r" % exc
            self.sign_logger.error("签到异常: %r" % exc)

    # ---------------------------------------------------------- 抽奖

    def daily_exchange(self):
        self.exchange_logger.info("执行日常卡片抽奖...")
        if not self.post_formhash:
            self.exchange_result = "失败：缺少 formhash"
            return
        url = self._url("/plugin.php?id=it618_award:ajax&ac=getaward&formhash=%s&_=%d"
                        % (self.post_formhash, int(time.time() * 1000)))
        headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "referer": self._url("/it618_award-award.html"),
            "x-requested-with": "XMLHttpRequest",
        }
        try:
            resp = self.sess.get(url, headers=headers)
            if is_gated(resp.text or ""):
                self.exchange_result = "失败：被验证门拦截"
                return
            data = resp.json()
            tipname = data.get("tipname")
            if tipname == "":
                self.exchange_result = "无奖励（今日或已抽奖）"
            elif tipname == "ok":
                self.exchange_result = "抽奖成功: %s" % data.get("tipvalue")
            else:
                self.exchange_result = "非预期响应: %s" % tipname
            self.exchange_logger.info("抽奖结果: %s" % self.exchange_result)
        except Exception as exc:  # noqa: BLE001
            self.exchange_result = "异常: %r" % exc
            self.exchange_logger.error("抽奖异常: %r" % exc)

    # ---------------------------------------------------------- 互动

    def visit_spaces(self):
        count = 0
        for uid in self.uids:
            try:
                self.sess.get(self._url("/space-uid-%s.html" % uid))
                count += 1
                time.sleep(1)
            except Exception as exc:  # noqa: BLE001
                self.task_logger.warning("访问空间 %s 失败: %r" % (uid, exc))
        return count

    def poke_users(self):
        count = 0
        for uid in self.uids:
            url = self._url("/home.php?mod=spacecp&ac=poke&op=send&uid=%s&inajax=1" % uid)
            data = {"formhash": self.post_formhash, "poke": "1",
                    "iconid": "3", "pokesubmit": "true"}
            try:
                if "succeed" in (self.sess.post(url, data=data,
                                                headers={"Referer": self._url("/")}).text or ""):
                    count += 1
                time.sleep(1)
            except Exception as exc:  # noqa: BLE001
                self.task_logger.warning("打招呼 %s 失败: %r" % (uid, exc))
        return count

    def stance_blogs(self, target=10, max_pages=3):
        count = 0
        page = 1
        while count < target and page <= max_pages:
            list_url = self._url("/home.php?mod=space&do=blog&view=all&catid=14&page=%d" % page)
            try:
                res = self.sess.get(list_url).text or ""
                if is_gated(res):
                    self.task_logger.warning("日志列表被验证门拦截，跳过表态")
                    break
                blog_urls = set(re.findall(
                    r"home\.php\?mod=space(?:&amp;|&)uid=\d+(?:&amp;|&)do=blog(?:&amp;|&)id=\d+",
                    res))
                if not blog_urls:
                    self.task_logger.debug("第 %d 页没有解析到日志链接" % page)
                for uri in blog_urls:
                    if count >= target:
                        break
                    try:
                        blog_res = self.sess.get(
                            self._url("/" + uri.replace("&amp;", "&"))).text or ""
                        click_match = re.search(
                            r"(home\.php\?mod=spacecp(?:&amp;|&)ac=click(?:&amp;|&)op=add[^\"']+)",
                            blog_res)
                        if not click_match:
                            continue
                        click_url = self._url("/" + click_match.group(1).replace("&amp;", "&"))
                        if "成功" in (self.sess.get(click_url, headers={
                                "x-requested-with": "XMLHttpRequest"}).text or ""):
                            count += 1
                        time.sleep(1)
                    except Exception as exc:  # noqa: BLE001
                        self.task_logger.warning("日志表态失败: %r" % exc)
            except Exception as exc:  # noqa: BLE001
                self.task_logger.warning("日志列表第 %d 页异常: %r" % (page, exc))
                break
            page += 1
        return count

    def draw_and_guess(self):
        url = self._url("/plugin.php?id=viewui_draw&mod=api&ac=adddraw")
        base64_img = ("data:image/png;base64,"
                      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADklEQVR4AWL6////fwAAAAD"
                      "//w7I1cwAAAAGSURBVAMACgUD/9k79a8AAAAASUVORK5CYII=")
        data = {"title": "水果", "answer": "苹果",
                "pic": base64_img, "formhash": self.post_formhash}
        headers = {
            "x-requested-with": "XMLHttpRequest",
            "origin": "https://%s" % self.hostname,
            "referer": self._url("/plugin.php?id=viewui_draw"),
        }
        try:
            resp = self.sess.post(url, data=data, headers=headers)
            if is_gated(resp.text or ""):
                return "失败：被验证门拦截"
            try:
                msg = resp.json().get("message", (resp.text or "")[:20])
            except Exception:  # noqa: BLE001
                msg = (resp.text or "")[:20]
            self.task_logger.debug("你画我猜返回: %s" % msg)
            if "成功" in msg or "succeed" in msg:
                return "出题成功"
            if "今日" in msg or "上限" in msg or "用完" in msg:
                return "额度已满"
            return "失败: %s" % str(msg)[:30]
        except Exception as exc:  # noqa: BLE001
            return "提交异常: %r" % exc

    def execute_interactive_tasks(self):
        self.task_logger.info("开始执行互动作业...")
        s_count = self.visit_spaces()
        p_count = self.poke_users()
        b_count = self.stance_blogs(target=10, max_pages=3)
        d_status = self.draw_and_guess()
        self.task_result = ("空间访问(%d/3) | 打招呼(%d/3) | 日志表态(%d/10) | 你画我猜(%s)"
                            % (s_count, p_count, b_count, d_status))
        self.task_logger.info("互动作业结果: %s" % self.task_result)

    # ---------------------------------------------------------- 资产

    @staticmethod
    def _extract_assets(clean_text):
        """
        从去标签后的文本里抓 8 项资产。
        原版对全文无锚点直接匹配，"金币"二字出现在任何位置都可能被误抓；
        这里保留"名称 + 可选分隔符 + 数字"的匹配，同时记录命中上下文便于排障，
        并允许未命中的项返回 None（而不是静默记 0）。
        """
        assets = {}
        contexts = {}
        for item in ASSET_ITEMS:
            m = re.search(r"%s\s*[:：=]?\s*(\d{1,9})" % re.escape(item), clean_text)
            if m:
                assets[item] = int(m.group(1))
                start = max(0, m.start() - 20)
                contexts[item] = re.sub(r"\s+", " ", clean_text[start:m.end() + 10])
            else:
                assets[item] = None
        return assets, contexts

    def fetch_assets(self):
        self.task_logger.info("正在获取个人资产数据...")
        url = self._url("/home.php?mod=spacecp&ac=credit&op=base")
        try:
            res = self.sess.get(url).text or ""
            # 顺手记下自己的 uid（页面里的 discuz_uid），用于给基准文件打归属标识
            uid = detect_uid_in_html(res)
            if uid:
                self.my_uid = uid
            if is_gated(res):
                self.assets_report = "资产抓取失败：被验证门拦截"
                self.task_logger.error(self.assets_report)
                return

            clean_text = re.sub(r"<script.*?</script>", " ", res, flags=re.S | re.I)
            clean_text = re.sub(r"<style.*?</style>", " ", clean_text, flags=re.S | re.I)
            clean_text = re.sub(r"<[^>]+>", " ", clean_text)
            clean_text = re.sub(r"&nbsp;?", " ", clean_text)
            clean_text = re.sub(r"[ \t\u3000]+", " ", clean_text)

            if os.getenv("GM_DEBUG_ASSETS", "").strip() in ("1", "true", "yes"):
                try:
                    with open("assets_debug.txt", "w", encoding="utf-8") as fh:
                        fh.write(clean_text)
                    self.task_logger.info("已导出 assets_debug.txt 供人工核对")
                except Exception as exc:  # noqa: BLE001
                    self.task_logger.warning("导出 assets_debug.txt 失败: %r" % exc)

            assets, contexts = self._extract_assets(clean_text)
            for item in ASSET_ITEMS:
                if contexts.get(item):
                    self.task_logger.debug("资产命中 %s -> %s" % (item, contexts[item]))

            if assets.get("金币") is None:
                self.assets_ok = False
                self.assets_report = ("资产抓取失败：未能解析到金币数量"
                                      "（页面 %d 字节，可能未登录或页面结构变化）" % len(res))
                self.task_logger.error(self.assets_report)
                return

            current_gold = assets["金币"]
            current_blood = assets.get("血液")

            # 积分：积分页不一定带这一项，抓不到就补抓一次空间首页（那里有「积分: N」）
            credit = assets.get("积分")
            if credit is None:
                credit = self._fetch_credit_from_space()
                if credit is not None:
                    assets["积分"] = credit

            # 读取上次基准（首次运行 / 文件损坏 / 别人仓库里的基准都不会报错，只是不显示增减）
            owner_ids = build_asset_owner(self.my_uid, self.username)
            if owner_ids:
                self.task_logger.debug("本账号基准指纹: %s" % ",".join(owner_ids))
            record = load_asset_record(self.task_logger, owner_ids)
            prev = {}
            for key in TRACKED_ITEMS:
                raw = record.get(key)
                prev[key] = int(raw) if isinstance(raw, int) else None

            # 仅在解析成功时回写基准，避免把 0 或错值写进去污染后续对比
            new_record = {"date": datetime.date.today().isoformat(), "金币": current_gold}
            if current_blood is not None:
                new_record["血液"] = current_blood
            if credit is not None:
                new_record["积分"] = credit
            # 归属指纹取并集：某次读不到 uid 不会抹掉已记录的指纹，
            # 避免「这次认得出、下次认不出」导致基准被反复重置。
            old_owner = record.get("_owner")
            old_owner = [old_owner] if isinstance(old_owner, str) else list(old_owner or [])
            merged_owner = sorted(set(owner_ids) | set(old_owner))
            if merged_owner:
                new_record["_owner"] = merged_owner
            save_asset_record(new_record, self.task_logger)

            def v(k):
                return assets[k] if assets[k] is not None else "?"

            lines = [
                "💰 金币: %s%s" % (current_gold, fmt_growth(current_gold, prev["金币"])),
                "🩸 血液: %s%s" % (v("血液"), fmt_growth(current_blood, prev["血液"])),
                "✈️ 旅程: %s  |  👣 追随: %s" % (v("旅程"), v("追随")),
                "📚 知识: %s  |  🔮 咒术: %s" % (v("知识"), v("咒术")),
                "🖤 堕落: %s  |  👻 灵魂: %s" % (v("堕落"), v("灵魂")),
            ]

            if credit is None:
                lines.append("")
                lines.append("🏅 积分: 未解析到"
                             "（页面结构可能变化，可设 GM_DEBUG_ASSETS=1 导出页面核对）")
            else:
                lv, _cur_th, nxt_th = level_of(credit)
                lines.append("")
                lines.append("🏅 积分: %d%s  |  🎖️ 等级: Lvl. %d"
                             % (credit, fmt_growth(credit, prev["积分"]), lv))
                if nxt_th is None:
                    lines.append("📈 已满级（Lvl. %d），无需再冲分 🎉" % lv)
                else:
                    need_point = nxt_th - credit
                    need_blood = need_point * BLOOD_PER_POINT
                    lines.append("📈 距 Lvl. %d 还需 %d 积分（门槛 %d）"
                                 % (lv + 1, need_point, nxt_th))
                    if current_blood is None:
                        lines.append("🔥 献祭估算: 约需 %d 血液"
                                     "（按 1 积分 = %d 血液折算）"
                                     % (need_blood, BLOOD_PER_POINT))
                    elif current_blood >= need_blood:
                        lines.append("🔥 献祭估算: 约需 %d 血液 → 当前 %d 点，"
                                     "血量足够，献祭即可升级 ✅" % (need_blood, current_blood))
                    else:
                        lines.append("🔥 献祭估算: 约需 %d 血液 → 当前 %d 点，尚差 %d ⏳"
                                     % (need_blood, current_blood, need_blood - current_blood))

            self.assets_report = "\n".join(lines)
            self.assets_ok = True
        except Exception as exc:  # noqa: BLE001
            self.assets_report = "资产抓取异常: %r" % exc
            self.task_logger.error(self.assets_report)
        self.task_logger.info("当前账户综合看板:\n%s" % self.assets_report)

    def _fetch_credit_from_space(self):
        """
        从空间首页补抓「积分」（v1.1 新增）。

        积分页（spacecp&ac=credit&op=base）不一定列出「积分」这一项，
        而空间首页的「统计信息」区块里是明确有的（形如「积分: 126 | 旅程: 93」）。
        全程只读；失败仅告警并返回 None，不影响主流程与邮件发送。
        """
        try:
            res = self.sess.get(self._url("/home.php?mod=space")).text or ""
            uid = detect_uid_in_html(res)
            if uid:
                self.my_uid = uid
            if is_gated(res):
                self.task_logger.warning("空间首页被验证门拦截，积分未能补抓")
                return None
            text = re.sub(r"<script.*?</script>", " ", res, flags=re.S | re.I)
            text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"&nbsp;?", " ", text)
            m = re.search(r"积分\s*[:：=]?\s*(\d{1,9})", text)
            if m:
                self.task_logger.debug("空间首页补抓到积分 -> %s" % m.group(1))
                return int(m.group(1))
            self.task_logger.warning("空间首页未找到「积分」字段")
        except Exception as exc:  # noqa: BLE001
            self.task_logger.warning("补抓积分失败: %r" % exc)
        return None

    # ---------------------------------------------------------- 邮件

    def build_mail_content(self, status=None):
        """组装邮件 HTML 正文（独立出来便于本地预览与单元测试）。"""
        if status is None:
            status = "成功" if (self.logged_in and not self.fatal_error) else "异常"

        fail_block = ""
        if self.fatal_error:
            fail_block = ("<p style='color:#c00;'><b>中断原因:</b> %s</p>"
                          % self.fatal_error.replace("<", "&lt;"))

        return (
            "<h3>🎮 GameMale 每日自动化任务报告</h3>"
            "<p>🔧 <b>运行模式:</b> %s | 🚪 <b>验证门:</b> %s | 📌 <b>总体:</b> %s</p>"
            "%s"
            "<p>🔑 <b>登录:</b> %s</p>"
            "<p>📝 <b>核心签到:</b> %s</p>"
            "<p>🎁 <b>日常抽奖:</b> %s</p>"
            "<p>🤝 <b>互动作业:</b> %s</p>"
            "<br><h4>📊 当前核心资产状态：</h4>"
            "<pre style='background:#f4f4f4;padding:15px;border-radius:5px;"
            "font-family:monospace;line-height:1.6;font-size:14px;'>%s</pre>"
            "<br><small style='color:#888;'>报告由 GM-All-In-One %s 生成</small>"
            % (self.run_mode, self.gate_mode, status, fail_block,
               "成功" if self.logged_in else "失败",
               self.sign_result, self.exchange_result, self.task_result,
               self.assets_report.replace("<", "&lt;"), VERSION)
        )

    def send_notification(self):
        smtp_host = env("GM_SMTP_HOST", "SMTP_HOST")
        mail_user = env("GM_MAIL_USER", "MAIL_USER")
        mail_pass = env("GM_MAIL_PASS", "MAIL_PASS")
        mail_to = env("GM_MAIL_TO", "MAIL_TO") or mail_user

        if not all([smtp_host, mail_user, mail_pass]):
            self.notice_logger.warning("未配置完整的 SMTP_HOST / MAIL_USER / MAIL_PASS，跳过邮件通知")
            return False

        status = "成功" if (self.logged_in and not self.fatal_error) else "异常"
        self.notice_logger.info("发送推送邮件至 %s ..." % mail_to)

        mail_content = self.build_mail_content(status)

        message = MIMEText(mail_content, "html", "utf-8")
        message["From"] = formataddr((Header("GM-Bot", "utf-8").encode(), mail_user))
        message["To"] = formataddr((Header("Master", "utf-8").encode(), mail_to))
        message["Subject"] = Header(
            "🎮 GameMale 任务运行报告 - %s [%s]" % (status, self.sign_result), "utf-8")
        try:
            server = smtplib.SMTP_SSL(smtp_host, 465, timeout=30)
            server.login(mail_user, mail_pass)
            server.sendmail(mail_user, [mail_to], message.as_string())
            server.quit()
            self.notice_logger.info("推送邮件发送成功")
            return True
        except Exception as exc:  # noqa: BLE001
            self.notice_logger.error("推送邮件发送失败: %r" % exc)
            return False

    # ---------------------------------------------------------- 主流程

    def run(self, send_mail=True):
        self.main_logger.info("=== GM-All-In-One %s 任务引擎启动（模式: %s）==="
                              % (VERSION, self.run_mode))
        ok = True
        try:
            if not self.connect():
                ok = False
            elif not self.logged_in and not self.login():
                self.fatal_error = self.fatal_error or "登录失败"
                ok = False
            else:
                if self.logged_in and self.gate_mode == USER_COOKIE_MODE:
                    self.main_logger.info(
                        "用户 Cookie 模式：已带登录态，跳过登录流程"
                        "（因此不会触碰被 403 封死的 %s）"
                        % ", ".join(SPIDER_BLOCKED_PATHS))
                if self.run_mode in ("light", "check"):
                    self.main_logger.info("%s 模式：跳过签到/抽奖/互动，仅抓取资产" % self.run_mode)
                else:
                    self.sign_gamemale()
                    self.daily_exchange()
                    self.execute_interactive_tasks()
                self.fetch_assets()
                if not self.assets_ok:
                    ok = False
        except Exception as exc:  # noqa: BLE001
            self.fatal_error = "未捕获异常: %r" % exc
            self.main_logger.error(self.fatal_error)
            ok = False
        finally:
            if send_mail:
                self.send_notification()
            self.gate.close()
        self.main_logger.info("=== 任务结束（%s）===" % ("成功" if ok else "存在失败项"))
        return ok


# =============================================================== 入口


def main():
    parser = argparse.ArgumentParser(
        description="GameMale 论坛自动签到 %s" % VERSION)
    parser.add_argument("--check", action="store_true",
                        help="自检模式：破门 + 登录 + 抓资产，不发邮件、不做写操作")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--no-mail", action="store_true", help="本次不发邮件")
    parser.add_argument("--reset-record", action="store_true",
                        help="清空资产对比基准（克隆/复刻本仓库后，想让第一份报告是"
                             "「首次记录」时用；不需要网络与账号）")
    args = parser.parse_args()

    if args.reset_record:
        removed = []
        for path in (ASSET_RECORD_JSON, ASSET_RECORD_LEGACY):
            if os.path.exists(path):
                try:
                    os.remove(path)
                    removed.append(path)
                except Exception as exc:  # noqa: BLE001
                    print("删除 %s 失败: %r" % (path, exc))
                    return 1
        print("已清空资产对比基准: %s"
              % (", ".join(removed) if removed else "（本来就没有，无需清空）"))
        print("下次运行会全部显示「首次记录」，这是预期行为。")
        return 0

    username = env("GM_USERNAME", "GM_USER", "USERNAME")
    password = env("GM_PASSWORD", "GM_PASS", "PASSWORD")

    if not username or not password:
        print("缺少账号配置：请设置 GM_USERNAME / GM_PASSWORD"
              "（Windows 上 USERNAME 是系统内置变量，本地请用 GM_USERNAME）")
        return 1

    gm = Gamemale(
        username=username,
        password=password,
        questionid=env("GM_QUESTIONID", default="0"),
        answer=env("GM_ANSWER", default=""),
        verbose=args.verbose,
        hostname=env("GM_HOST", default=DEFAULT_HOST),
        run_mode=env("GM_RUN_MODE", default="check" if args.check else "full"),
        uids=parse_uids(env("GM_UIDS")),
        chrome_path=env("GM_CHROME_PATH"),
    )
    if args.check:
        gm.run_mode = "check"
        gm.main_logger.info("自检模式：破门 → 登录 → 抓资产，不发邮件、不执行写操作")
        ok = gm.run(send_mail=False)
    else:
        ok = gm.run(send_mail=not args.no_mail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
