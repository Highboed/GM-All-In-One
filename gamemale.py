# -*- coding: utf-8 -*-
"""
GM-All-In-One（修复版）—— GameMale 论坛签到一条龙

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
"""

import argparse
import hashlib
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
ASSET_ITEMS = ["金币", "血液", "旅程", "追随", "知识", "咒术", "堕落", "灵魂"]


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
            last_gold = current_gold
            if os.path.exists("gold_record.txt"):
                try:
                    with open("gold_record.txt", "r", encoding="utf-8") as fh:
                        content = fh.read().strip()
                    if content.isdigit():
                        last_gold = int(content)
                except Exception as exc:  # noqa: BLE001
                    self.task_logger.warning("读取 gold_record.txt 失败: %r" % exc)

            growth = current_gold - last_gold
            growth_str = "+%d" % growth if growth >= 0 else str(growth)

            # 仅在解析成功时回写基准，避免把 0 或错值写进去污染后续对比
            try:
                with open("gold_record.txt", "w", encoding="utf-8") as fh:
                    fh.write(str(current_gold))
            except Exception as exc:  # noqa: BLE001
                self.task_logger.warning("写入 gold_record.txt 失败: %r" % exc)

            def v(k):
                return assets[k] if assets[k] is not None else "?"

            self.assets_report = (
                "金币: %s (较上次 %s)\n"
                "血液: %s | 旅程: %s | 追随: %s\n"
                "知识: %s | 咒术: %s | 堕落: %s\n"
                "灵魂: %s"
                % (current_gold, growth_str, v("血液"), v("旅程"), v("追随"),
                   v("知识"), v("咒术"), v("堕落"), v("灵魂"))
            )
            self.assets_ok = True
        except Exception as exc:  # noqa: BLE001
            self.assets_report = "资产抓取异常: %r" % exc
            self.task_logger.error(self.assets_report)
        self.task_logger.info("当前账户综合看板:\n%s" % self.assets_report)

    # ---------------------------------------------------------- 邮件

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

        fail_block = ""
        if self.fatal_error:
            fail_block = ("<p style='color:#c00;'><b>中断原因:</b> %s</p>"
                          % self.fatal_error.replace("<", "&lt;"))

        mail_content = (
            "<h3>GameMale 每日自动化任务报告</h3>"
            "<p><b>运行模式:</b> %s | <b>验证门:</b> %s | <b>总体:</b> %s</p>"
            "%s"
            "<p><b>登录:</b> %s</p>"
            "<p><b>核心签到:</b> %s</p>"
            "<p><b>日常抽奖:</b> %s</p>"
            "<p><b>互动作业:</b> %s</p>"
            "<br><h4>当前核心资产状态：</h4>"
            "<pre style='background:#f4f4f4;padding:15px;border-radius:5px;"
            "font-family:monospace;line-height:1.6;font-size:14px;'>%s</pre>"
            "<br><small style='color:#888;'>报告由 GM-All-In-One（修复版）生成</small>"
            % (self.run_mode, self.gate_mode, status, fail_block,
               "成功" if self.logged_in else "失败",
               self.sign_result, self.exchange_result, self.task_result,
               self.assets_report.replace("<", "&lt;"))
        )

        message = MIMEText(mail_content, "html", "utf-8")
        message["From"] = formataddr((Header("GM-Bot", "utf-8").encode(), mail_user))
        message["To"] = formataddr((Header("Master", "utf-8").encode(), mail_to))
        message["Subject"] = Header(
            "GameMale 任务运行报告 - %s [%s]" % (status, self.sign_result), "utf-8")
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
        self.main_logger.info("=== GM-All-In-One 任务引擎启动（模式: %s）===" % self.run_mode)
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
    parser = argparse.ArgumentParser(description="GameMale 论坛自动签到（修复版）")
    parser.add_argument("--check", action="store_true",
                        help="自检模式：破门 + 登录 + 抓资产，不发邮件、不做写操作")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--no-mail", action="store_true", help="本次不发邮件")
    args = parser.parse_args()

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
