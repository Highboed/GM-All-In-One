import logging
import requests
import re
import ddddocr
import os
import time
import smtplib
import shutil
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from DrissionPage import ChromiumPage, ChromiumOptions


def setup_logger(name, verbose=False):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if logger.handlers:
        logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-10s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


class Gamemale:
    def __init__(
        self,
        username,
        password,
        questionid='0',
        answer=None,
        verbose=False
    ):
        self.verbose = verbose

        self.main_logger = setup_logger('GameMale', verbose)
        self.login_logger = setup_logger('登录', verbose)
        self.sign_logger = setup_logger('签到', verbose)
        self.exchange_logger = setup_logger('抽奖', verbose)
        self.task_logger = setup_logger('日常任务', verbose)
        self.notice_logger = setup_logger('通知', verbose)

        self.ocr = ddddocr.DdddOcr(show_ad=False)

        self.post_formhash = None

        self.sign_result = "未执行"
        self.exchange_result = "未执行"
        self.task_result = "未执行"
        self.assets_report = "未抓取"

        self.username = str(username)
        self.password = str(password)

        self.questionid = questionid
        self.answer = str(answer) if answer else ""

        self.hostname = "www.gamemale.com"

        self.session = requests.Session()

        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        })

    # ============================================================
    # Cloudflare
    # ============================================================

    def _find_chrome(self):
        """
        GitHub Actions ubuntu-latest 常见 Chrome 路径自动检测。
        """
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]

        for path in candidates:
            if os.path.exists(path):
                return path

        chrome = shutil.which("google-chrome")
        if chrome:
            return chrome

        chrome = shutil.which("google-chrome-stable")
        if chrome:
            return chrome

        chrome = shutil.which("chromium")
        if chrome:
            return chrome

        chrome = shutil.which("chromium-browser")
        if chrome:
            return chrome

        return None

    def bypass_cloudflare(self):
        """
        使用独立 Chrome + 固定远程调试端口启动浏览器，
        避免 DrissionPage 在 GitHub Actions 中出现：

        WebSocketBadStatusException:
        Handshake status 404 Not Found
        """

        self.main_logger.info(
            "启动真实 Chrome 浏览器，准备突破 Cloudflare 盾..."
        )

        chrome_path = self._find_chrome()

        if not chrome_path:
            self.main_logger.error(
                "GitHub Actions 环境中未找到 Chrome/Chromium"
            )
            return False

        self.main_logger.info(f"Chrome 路径: {chrome_path}")

        co = ChromiumOptions()

        # 明确指定浏览器
        co.set_paths(browser_path=chrome_path)

        # GitHub Actions 无 GUI 环境
        co.set_argument('--headless=new')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        co.set_argument('--disable-dev-shm-usage')

        # 避免共享内存及自动化环境问题
        co.set_argument('--disable-software-rasterizer')
        co.set_argument('--disable-background-networking')
        co.set_argument('--disable-background-timer-throttling')
        co.set_argument('--disable-renderer-backgrounding')
        co.set_argument('--disable-backgrounding-occluded-windows')

        # 固定窗口尺寸
        co.set_argument('--window-size=1920,1080')

        # 固定远程调试端口，避免 DrissionPage 获取到错误 WS 地址
        co.set_local_port(9222)

        page = None

        try:
            page = ChromiumPage(co)

            target_url = f"https://{self.hostname}/forum.php"

            self.main_logger.info(
                f"访问目标网站: {target_url}"
            )

            page.get(target_url)

            self.main_logger.info(
                "正在等待 Cloudflare 验证通过..."
            )

            # 给 Cloudflare JS 足够的执行时间
            start_time = time.time()
            max_wait = 30

            while time.time() - start_time < max_wait:
                try:
                    title = page.title or ""
                    url = page.url or ""

                    self.main_logger.info(
                        f"当前页面标题: {title}"
                    )

                    # Cloudflare 验证完成后的常见情况
                    if (
                        "Just a moment" not in title
                        and "Attention Required" not in title
                        and "cf-chl" not in url
                    ):
                        break

                except Exception:
                    pass

                time.sleep(2)

            # 再额外等待 Cookie 写入
            time.sleep(3)

            # 获取 Cookie
            cookies = page.cookies(as_dict=True)

            if not cookies:
                self.main_logger.error(
                    "Chrome 未获取到任何 Cookie"
                )
                return False

            # 获取真实 UA
            try:
                ua = page.user_agent
            except Exception:
                ua = None

            self.session.cookies.update(cookies)

            if ua:
                self.session.headers.update({
                    'User-Agent': ua
                })

            self.main_logger.info(
                f"Cloudflare 浏览器会话接管成功，获取 Cookie: {len(cookies)} 个"
            )

            # 简单验证 Cookie 是否可以被 requests 使用
            try:
                check = self.session.get(
                    target_url,
                    timeout=20
                )

                if check.status_code < 500:
                    self.main_logger.info(
                        f"底层 requests 会话验证完成，HTTP {check.status_code}"
                    )
                else:
                    self.main_logger.warning(
                        f"底层 requests 会话返回 HTTP {check.status_code}"
                    )

            except Exception as e:
                self.main_logger.warning(
                    f"底层 requests 会话验证异常: {e}"
                )

            return True

        except Exception as e:
            self.main_logger.error(
                f"Chrome / Cloudflare 处理异常: {e}"
            )
            return False

        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass

    # ============================================================
    # Login
    # ============================================================

    def get_login_formhash(self):
        url = (
            f"https://{self.hostname}"
            "/member.php?mod=logging&action=login"
        )

        response = self.session.get(
            url,
            timeout=20
        )

        text = response.text

        loginhash_match = re.search(
            r'<div id="main_messaqge_(.+?)">',
            text
        )

        formhash_match = re.search(
            r'<input type="hidden" name="formhash" value="(.+?)" />',
            text
        )

        if not loginhash_match or not formhash_match:
            raise ValueError(
                "无法获取 loginhash 或 formhash"
            )

        return (
            loginhash_match.group(1),
            formhash_match.group(1)
        )

    def verify_code(self, max_retries=10) -> str:
        self.login_logger.info(
            f"正在识别验证码 [最大重试次数: {max_retries}]"
        )

        for attempt in range(1, max_retries + 1):

            update_url = (
                f"https://{self.hostname}"
                "/misc.php?mod=seccode"
                "&action=update"
                "&idhash=cSA"
                "&0.1234567"
                "&modid=member::logging"
            )

            try:
                update_text = self.session.get(
                    update_url,
                    timeout=20
                ).text
            except Exception:
                continue

            update_match = re.search(
                r'update=(.+?)&idhash=',
                update_text
            )

            if not update_match:
                continue

            update_value = update_match.group(1)

            code_url = (
                f"https://{self.hostname}"
                f"/misc.php?mod=seccode"
                f"&update={update_value}"
                "&idhash=cSA"
            )

            headers = {
                'Accept': (
                    'image/webp,image/apng,image/*,'
                    '*/*;q=0.8'
                ),
                'Referer': (
                    f"https://{self.hostname}"
                    "/member.php?mod=logging&action=login"
                ),
            }

            try:
                code_resp = self.session.get(
                    code_url,
                    headers=headers,
                    timeout=20
                )
            except Exception:
                continue

            if not code_resp.content:
                continue

            try:
                code = self.ocr.classification(
                    code_resp.content
                )
            except Exception:
                continue

            code = re.sub(
                r'[^A-Za-z0-9]',
                '',
                code
            )

            if not code:
                continue

            verify_url = (
                f"https://{self.hostname}"
                "/misc.php?mod=seccode"
                "&action=check"
                "&inajax=1"
                "&modid=member::logging"
                "&idhash=cSA"
                f"&secverify={code}"
            )

            try:
                verify_text = self.session.get(
                    verify_url,
                    timeout=20
                ).text
            except Exception:
                continue

            if "succeed" in verify_text:
                self.login_logger.info(
                    f"验证码识别成功: {code} "
                    f"(尝试第 {attempt} 次)"
                )
                return code

            time.sleep(1)

        return ""

    def login(self) -> bool:
        self.login_logger.info(
            "开始登录流程..."
        )

        code = self.verify_code()

        if not code:
            self.login_logger.error(
                "验证码识别失败，中止登录"
            )
            return False

        try:
            loginhash, formhash = self.get_login_formhash()
        except Exception as e:
            self.login_logger.error(
                f"获取登录参数失败: {e}"
            )
            return False

        login_url = (
            f"https://{self.hostname}"
            "/member.php?mod=logging"
            "&action=login"
            "&loginsubmit=yes"
            f"&loginhash={loginhash}"
            "&inajax=1"
        )

        form_data = {
            'formhash': formhash,
            'referer': f"https://{self.hostname}/",
            'loginfield': self.username,
            'username': self.username,
            'password': self.password,
            'questionid': self.questionid,
            'answer': self.answer,
            'cookietime': 2592000,
            'seccodehash': 'cSA',
            'seccodemodid': 'member::logging',
            'seccodeverify': code,
        }

        try:
            resp_text = self.session.post(
                login_url,
                data=form_data,
                timeout=20
            ).text
        except Exception as e:
            self.login_logger.error(
                f"登录请求异常: {e}"
            )
            return False

        if "succeed" in resp_text:
            self.login_logger.info(
                "登录成功"
            )

            try:
                text = self.session.get(
                    f"https://{self.hostname}/forum.php",
                    timeout=20
                ).text

                formhash_match = re.search(
                    r'<input type="hidden" name="formhash" value="(.+?)" />',
                    text
                )

                if formhash_match:
                    self.post_formhash = (
                        formhash_match.group(1)
                    )

                    self.login_logger.info(
                        "全局 formhash 获取成功"
                    )
                else:
                    self.login_logger.warning(
                        "登录成功，但未能提取全局 formhash"
                    )

            except Exception as e:
                self.login_logger.error(
                    f"提取全局 formhash 失败: {e}"
                )

            return True

        self.login_logger.error(
            "登录失败，请检查凭证或安全提问设置"
        )

        return False

    # ============================================================
    # Daily Sign-in
    # ============================================================

    def sign_gamemale(self):
        self.sign_logger.info(
            "执行每日签到..."
        )

        if not self.post_formhash:
            self.sign_result = "失败：缺少 formhash"
            return

        url = (
            f"https://{self.hostname}"
            "/k_misign-sign.html"
            "?operation=qiandao"
            "&format=button"
            f"&formhash={self.post_formhash}"
        )

        try:
            res = self.session.get(
                url,
                timeout=20
            ).text

            if "签到成功" in res:
                self.sign_result = "签到成功"

            elif "已签" in res:
                self.sign_result = "今日已签到"

            else:
                self.sign_result = "未知响应状态"

            self.sign_logger.info(
                f"签到结果: {self.sign_result}"
            )

        except Exception as e:
            self.sign_result = f"异常: {e}"

            self.sign_logger.error(
                f"签到异常: {e}"
            )

    # ============================================================
    # Daily Exchange
    # ============================================================

    def daily_exchange(self):
        self.exchange_logger.info(
            "执行日常卡片抽奖..."
        )

        if not self.post_formhash:
            self.exchange_result = "失败：缺少 formhash"
            return

        timestamp = str(
            int(time.time() * 1000)
        )

        url = (
            f"https://{self.hostname}"
            "/plugin.php?id=it618_award:ajax"
            "&ac=getaward"
            f"&formhash={self.post_formhash}"
            f"&_={timestamp}"
        )

        headers = {
            'accept': (
                'application/json, text/javascript, '
                '*/*; q=0.01'
            ),
            'referer': (
                f"https://{self.hostname}"
                "/it618_award-award.html"
            ),
            'x-requested-with': 'XMLHttpRequest',
        }

        try:
            response = self.session.get(
                url,
                headers=headers,
                timeout=20
            )

            res_json = response.json()

            tipname = res_json.get("tipname")

            if tipname == "":
                self.exchange_result = (
                    "无奖励（今日或已抽奖）"
                )

            elif tipname == "ok":
                self.exchange_result = (
                    f"抽奖成功: "
                    f"{res_json.get('tipvalue')}"
                )

            else:
                self.exchange_result = (
                    f"非预期响应: {tipname}"
                )

            self.exchange_logger.info(
                f"抽奖结果: {self.exchange_result}"
            )

        except Exception as e:
            self.exchange_result = f"异常: {e}"

            self.exchange_logger.error(
                f"抽奖异常: {e}"
            )

    # ============================================================
    # Visit Spaces
    # ============================================================

    def visit_spaces(self):
        uids = [
            730713,
            62445,
            61832
        ]

        count = 0

        for uid in uids:
            try:
                url = (
                    f"https://{self.hostname}"
                    f"/space-uid-{uid}.html"
                )

                self.session.get(
                    url,
                    timeout=20
                )

                count += 1

                time.sleep(1)

            except Exception:
                pass

        return count

    # ============================================================
    # Poke Users
    # ============================================================

    def poke_users(self):
        uids = [
            730713,
            62445,
            61832
        ]

        count = 0

        for uid in uids:

            url = (
                f"https://{self.hostname}"
                "/home.php?mod=spacecp"
                "&ac=poke"
                "&op=send"
                f"&uid={uid}"
                "&inajax=1"
            )

            data = {
                'formhash': self.post_formhash,
                'poke': '1',
                'iconid': '3',
                'pokesubmit': 'true'
            }

            try:
                response = self.session.post(
                    url,
                    data=data,
                    timeout=20
                )

                if "succeed" in response.text:
                    count += 1

                time.sleep(1)

            except Exception:
                pass

        return count

    # ============================================================
    # Blogs
    # ============================================================

    def stance_blogs(self):
        count = 1
        page = 1

        while count < 10 and page <= 3:

            list_url = (
                f"https://{self.hostname}"
                "/home.php?mod=space"
                "&do=blog"
                "&view=all"
                "&catid=14"
                f"&page={page}"
            )

            try:
                res = self.session.get(
                    list_url,
                    timeout=20
                ).text

                blog_urls = set(
                    re.findall(
                        r'home\.php\?mod=space'
                        r'(?:&amp;|&)uid=\d+'
                        r'(?:&amp;|&)do=blog'
                        r'(?:&amp;|&)id=\d+',
                        res
                    )
                )

                for uri in blog_urls:

                    if count >= 10:
                        break

                    clean_uri = uri.replace(
                        '&amp;',
                        '&'
                    )

                    blog_url = (
                        f"https://{self.hostname}/"
                        f"{clean_uri}"
                    )

                    blog_res = self.session.get(
                        blog_url,
                        timeout=20
                    ).text

                    click_match = re.search(
                        r'(home\.php\?mod=spacecp'
                        r'(?:&amp;|&)ac=click'
                        r'(?:&amp;|&)op=add[^"\']+)',
                        blog_res
                    )

                    if click_match:

                        click_url = (
                            f"https://{self.hostname}/"
                            f"{click_match.group(1).replace('&amp;', '&')}"
                        )

                        click_response = self.session.get(
                            click_url,
                            headers={
                                'x-requested-with':
                                    'XMLHttpRequest'
                            },
                            timeout=20
                        )

                        if "成功" in click_response.text:
                            count += 1

                    time.sleep(1)

            except Exception:
                break

            page += 1

        return count

    # ============================================================
    # Draw And Guess
    # ============================================================

    def draw_and_guess(self):
        url = (
            f"https://{self.hostname}"
            "/plugin.php?id=viewui_draw"
            "&mod=api"
            "&ac=adddraw"
        )

        base64_img = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADklEQVR4AWL6////fwAAAAD//w7I1cwAAAAGSURBVAMACgUD"
            "/9k79a8AAAAASUVORK5CYII="
        )

        data = {
            'title': '水果',
            'answer': '苹果',
            'pic': base64_img,
            'formhash': self.post_formhash
        }

        headers = {
            'x-requested-with': 'XMLHttpRequest',
            'origin': f"https://{self.hostname}",
            'referer': (
                f"https://{self.hostname}"
                "/plugin.php?id=viewui_draw"
            )
        }

        try:
            response = self.session.post(
                url,
                data=data,
                headers=headers,
                timeout=20
            )

            try:
                res_json = response.json()
                msg = res_json.get(
                    "message",
                    response.text[:20]
                )
            except Exception:
                msg = response.text[:20]

            self.task_logger.info(
                f"[Debug] 你画我猜真实返回: {msg}"
            )

            if (
                "成功" in msg
                or "succeed" in msg
            ):
                return "出题成功"

            elif (
                "今日" in msg
                or "上限" in msg
                or "用完" in msg
            ):
                return "额度已满"

            else:
                return f"失败: {msg[:10]}"

        except Exception as e:
            self.task_logger.error(
                f"你画我猜提交异常: {e}"
            )
            return "提交异常"

    # ============================================================
    # Assets
    # ============================================================

    def fetch_assets(self):
        self.task_logger.info(
            "正在获取实时个人资产数据 (极简稳定版)..."
        )

        url = (
            f"https://{self.hostname}"
            "/home.php?mod=spacecp"
            "&ac=credit"
            "&op=base"
        )

        try:
            res = self.session.get(
                url,
                timeout=20
            ).text

            clean_text = re.sub(
                r'<[^>]+>',
                '',
                res
            )

            assets_dict = {}

            for item in [
                '金币',
                '血液',
                '旅程',
                '追随',
                '知识',
                '咒术',
                '堕落',
                '灵魂'
            ]:
                match = re.search(
                    rf'{item}\s*[:：]?\s*(\d+)',
                    clean_text
                )

                assets_dict[item] = (
                    int(match.group(1))
                    if match
                    else 0
                )

            current_gold = assets_dict['金币']

            last_gold = current_gold

            if os.path.exists("gold_record.txt"):
                try:
                    with open(
                        "gold_record.txt",
                        "r",
                        encoding="utf-8"
                    ) as f:
                        content = f.read().strip()

                    if content.isdigit():
                        last_gold = int(content)

                except Exception:
                    pass

            growth = current_gold - last_gold

            growth_str = (
                f"+{growth}"
                if growth >= 0
                else str(growth)
            )

            with open(
                "gold_record.txt",
                "w",
                encoding="utf-8"
            ) as f:
                f.write(
                    str(current_gold)
                )

            report = (
                f"💰 金币: {current_gold} "
                f"(较昨日 {growth_str})\n"
                f"🩸 血液: {assets_dict['血液']} | "
                f"✈️ 旅程: {assets_dict['旅程']} | "
                f"👣 追随: {assets_dict['追随']}\n"
                f"📚 知识: {assets_dict['知识']} | "
                f"🔮 咒术: {assets_dict['咒术']} | "
                f"🖤 堕落: {assets_dict['堕落']}\n"
                f"👻 灵魂: {assets_dict['灵魂']}"
            )

            self.assets_report = report

        except Exception as e:
            self.assets_report = (
                f"资产抓取异常: {e}"
            )

        self.task_logger.info(
            f"当前账户综合看板:\n"
            f"{self.assets_report}"
        )

    # ============================================================
    # Interactive Tasks
    # ============================================================

    def execute_interactive_tasks(self):
        self.task_logger.info(
            "开始执行互动作业..."
        )

        s_count = self.visit_spaces()
        p_count = self.poke_users()
        b_count = self.stance_blogs()
        d_status = self.draw_and_guess()

        self.task_result = (
            f"空间访问({s_count}/3) | "
            f"打招呼({p_count}/3) | "
            f"日志表态({b_count}/10) | "
            f"你画我猜({d_status})"
        )

        self.task_logger.info(
            f"互动作业结果: {self.task_result}"
        )

    # ============================================================
    # Email Notification
    # ============================================================

    def send_notification(self):
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = 465

        mail_user = os.getenv("MAIL_USER")
        mail_pass = os.getenv("MAIL_PASS")

        mail_to = os.getenv("MAIL_TO")

        if not mail_to or mail_to.strip() == "":
            mail_to = mail_user

        if not all([
            smtp_host,
            mail_user,
            mail_pass
        ]):
            self.notice_logger.warning(
                "未配置完整的 SMTP_HOST、发件人邮箱或授权码，"
                "跳过邮件通知流程"
            )
            return

        self.notice_logger.info(
            f"正在发送推送邮件至: {mail_to} ..."
        )

        mail_content = (
            "<h3>GameMale 每日自动化任务报告</h3>"

            f"<p><b>核心签到:</b> "
            f"{self.sign_result}</p>"

            f"<p><b>日常抽奖:</b> "
            f"{self.exchange_result}</p>"

            f"<p><b>互动作业:</b> "
            f"{self.task_result}</p>"

            "<br><h4>📊 当前核心资产状态：</h4>"

            "<pre style='"
            "background:#f4f4f4;"
            "padding:15px;"
            "border-radius:5px;"
            "font-family:monospace;"
            "line-height:1.6;"
            "font-size:14px;"
            "'>"

            f"{self.assets_report}"

            "</pre>"

            "<br><small style='color:#888;'>"
            "报告由 GM-All-In-One 自动化引擎生成"
            "</small>"
        )

        message = MIMEText(
            mail_content,
            'html',
            'utf-8'
        )

        message['From'] = formataddr(
            (
                Header(
                    "GM-Bot",
                    'utf-8'
                ).encode(),
                mail_user
            )
        )

        message['To'] = formataddr(
            (
                Header(
                    "Master",
                    'utf-8'
                ).encode(),
                mail_to
            )
        )

        message['Subject'] = Header(
            f"GameMale 任务运行报告 - "
            f"{self.sign_result}",
            'utf-8'
        )

        try:
            server = smtplib.SMTP_SSL(
                smtp_host,
                int(smtp_port),
                timeout=30
            )

            server.login(
                mail_user,
                mail_pass
            )

            server.sendmail(
                mail_user,
                [mail_to],
                message.as_string()
            )

            server.quit()

            self.notice_logger.info(
                "推送邮件发送成功！"
            )

        except Exception as e:
            self.notice_logger.error(
                f"推送邮件发送失败: {e}"
            )

    # ============================================================
    # Main
    # ============================================================

    def run(self):
        self.main_logger.info(
            "=== GM-All-In-One 任务引擎启动 ==="
        )

        # Cloudflare 浏览器会话
        if not self.bypass_cloudflare():
            self.main_logger.error(
                "Cloudflare 浏览器会话建立失败，"
                "中止本次任务。"
            )
            return

        # 登录
        if not self.login():
            self.send_notification()
            return

        # 每日签到
        self.sign_gamemale()

        # 日常抽奖
        self.daily_exchange()

        # 互动作业
        self.execute_interactive_tasks()

        # 获取资产
        self.fetch_assets()

        # 邮件通知
        self.send_notification()

        self.main_logger.info(
            "=== 所有作业同步执行完毕 ==="
        )


if __name__ == "__main__":
    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")

    if not username or not password:
        print(
            "错误：未检测到 USERNAME 或 PASSWORD Secrets"
        )
        exit(1)

    gm = Gamemale(
        username,
        password,
        verbose=False
    )

    gm.run()
