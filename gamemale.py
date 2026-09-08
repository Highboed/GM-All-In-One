import logging
import requests
import re
import ddddocr
import os
import time
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from DrissionPage import Chromium, ChromiumOptions


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
        self.questionid = str(questionid)
        self.answer = str(answer) if answer else ""

        self.hostname = "www.gamemale.com"
        self.base_url = f"https://{self.hostname}"

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (X11; Linux x86_64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/152.0.0.0 Safari/537.36'
            )
        })

        self.browser = None
        self.browser_tab = None

    def _get_chrome_port(self):
        port = os.getenv("CHROME_REMOTE_DEBUGGING_PORT", "9222")

        try:
            return int(port)
        except ValueError:
            return 9222

    def _chrome_available(self, port):
        try:
            response = requests.get(
                f"http://127.0.0.1:{port}/json/version",
                timeout=5
            )
            return response.ok
        except Exception:
            return False

    def bypass_cloudflare(self):
        """
        使用 GitHub Actions 已经启动的 Chrome。
        不重复启动第二个 Chrome，避免与 workflow 中的 remote debugging
        浏览器发生冲突。
        """
        self.main_logger.info(
            "连接 GitHub Actions 中已经启动的 Chrome，准备访问站点..."
        )

        port = self._get_chrome_port()

        self.main_logger.info(
            f"尝试连接 Chrome 调试端口: 127.0.0.1:{port}"
        )

        if not self._chrome_available(port):
            self.main_logger.error(
                f"Chrome DevTools 调试端口 {port} 不可访问。"
            )
            return False

        try:
            # DrissionPage 4.1.x 推荐通过 Chromium 对象接管已有浏览器。
            self.browser = Chromium(port)
            self.browser_tab = self.browser.latest_tab

            if not self.browser_tab:
                self.main_logger.error("没有找到可用的 Chrome 标签页。")
                return False

            self.browser_tab.get(
                f"{self.base_url}/forum.php",
                timeout=30
            )

            self.main_logger.info(
                "Chrome 已连接，等待页面完成加载..."
            )

            # 给页面以及浏览器 Cookie 一定的时间完成写入。
            deadline = time.time() + 30

            while time.time() < deadline:
                try:
                    title = self.browser_tab.title or ""
                except Exception:
                    title = ""

                if title and "Just a moment" not in title:
                    break

                time.sleep(2)

            # 再留出一点页面加载缓冲时间。
            time.sleep(3)

            try:
                cookies = self.browser.cookies().as_dict()
            except Exception:
                cookies = {}

            if not cookies:
                try:
                    cookies = self.browser_tab.cookies(
                        all_domains=True
                    ).as_dict()
                except Exception:
                    cookies = {}

            if not cookies:
                self.main_logger.error(
                    "Chrome 已连接，但没有读取到浏览器 Cookie。"
                )
                return False

            try:
                ua = self.browser_tab.user_agent
            except Exception:
                ua = self.session.headers.get('User-Agent')

            self.session.cookies.update(cookies)

            if ua:
                self.session.headers.update({
                    'User-Agent': ua
                })

            # 确认 requests 会话能够访问站点。
            check_response = self.session.get(
                f"{self.base_url}/forum.php",
                timeout=20
            )

            self.main_logger.info(
                f"站点访问状态: HTTP {check_response.status_code}"
            )

            self.main_logger.info(
                "Chrome 浏览器会话已成功接管。"
            )

            return True

        except Exception as e:
            self.main_logger.error(
                f"Chrome / 页面处理异常: {e}"
            )
            return False

    def get_login_formhash(self):
        url = (
            f"{self.base_url}/member.php"
            f"?mod=logging&action=login"
        )

        response = self.session.get(url, timeout=20)
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

    def verify_code(self, max_retries=10):
        self.login_logger.info(
            f"正在识别验证码 [最大重试次数: {max_retries}]"
        )

        for attempt in range(1, max_retries + 1):
            try:
                update_url = (
                    f"{self.base_url}/misc.php"
                    f"?mod=seccode"
                    f"&action=update"
                    f"&idhash=cSA"
                    f"&0.1234567"
                    f"&modid=member::logging"
                )

                update_text = self.session.get(
                    update_url,
                    timeout=15
                ).text

                update_match = re.search(
                    r'update=(.+?)&idhash=',
                    update_text
                )

                if not update_match:
                    self.login_logger.warning(
                        f"验证码地址获取失败，第 {attempt} 次"
                    )
                    continue

                update_value = update_match.group(1)

                code_url = (
                    f"{self.base_url}/misc.php"
                    f"?mod=seccode"
                    f"&update={update_value}"
                    f"&idhash=cSA"
                )

                headers = {
                    'Accept': (
                        'image/webp,image/apng,image/*,*/*;q=0.8'
                    ),
                    'Referer': (
                        f"{self.base_url}/member.php"
                        f"?mod=logging&action=login"
                    )
                }

                code_resp = self.session.get(
                    code_url,
                    headers=headers,
                    timeout=15
                )

                if not code_resp.content:
                    self.login_logger.warning(
                        f"验证码图片为空，第 {attempt} 次"
                    )
                    continue

                code = self.ocr.classification(
                    code_resp.content
                ).strip()

                if not code:
                    continue

                verify_url = (
                    f"{self.base_url}/misc.php"
                    f"?mod=seccode"
                    f"&action=check"
                    f"&inajax=1"
                    f"&modid=member::logging"
                    f"&idhash=cSA"
                    f"&secverify={code}"
                )

                verify_response = self.session.get(
                    verify_url,
                    timeout=15
                )

                if "succeed" in verify_response.text:
                    self.login_logger.info(
                        f"验证码识别成功: {code} "
                        f"(尝试第 {attempt} 次)"
                    )
                    return code

            except Exception as e:
                self.login_logger.warning(
                    f"验证码处理异常，第 {attempt} 次: {e}"
                )

            time.sleep(1)

        return ""

    def login(self):
        self.login_logger.info("开始登录流程...")

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
            f"{self.base_url}/member.php"
            f"?mod=logging"
            f"&action=login"
            f"&loginsubmit=yes"
            f"&loginhash={loginhash}"
            f"&inajax=1"
        )

        form_data = {
            'formhash': formhash,
            'referer': f"{self.base_url}/",
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
            response = self.session.post(
                login_url,
                data=form_data,
                timeout=20
            )

            resp_text = response.text

            if "succeed" in resp_text:
                self.login_logger.info("登录成功")

                try:
                    forum_response = self.session.get(
                        f"{self.base_url}/forum.php",
                        timeout=20
                    )

                    formhash_match = re.search(
                        r'<input type="hidden" name="formhash" '
                        r'value="(.+?)" />',
                        forum_response.text
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
                            "未找到全局 formhash"
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

        except Exception as e:
            self.login_logger.error(
                f"登录请求异常: {e}"
            )
            return False

    def sign_gamemale(self):
        self.sign_logger.info("执行每日签到...")

        if not self.post_formhash:
            self.sign_result = "失败：缺少 formhash"
            return

        url = (
            f"{self.base_url}/k_misign-sign.html"
            f"?operation=qiandao"
            f"&format=button"
            f"&formhash={self.post_formhash}"
        )

        try:
            response = self.session.get(
                url,
                timeout=20
            )
            res_text = response.text

            if "签到成功" in res_text:
                self.sign_result = "签到成功"
            elif "已签" in res_text:
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

    def daily_exchange(self):
        self.exchange_logger.info(
            "执行日常卡片抽奖..."
        )

        if not self.post_formhash:
            self.exchange_result = "失败：缺少 formhash"
            return

        url = (
            f"{self.base_url}/plugin.php"
            f"?id=it618_award:ajax"
            f"&ac=getaward"
            f"&formhash={self.post_formhash}"
            f"&_={int(time.time() * 1000)}"
        )

        headers = {
            'Accept': (
                'application/json, text/javascript, '
                '*/*; q=0.01'
            ),
            'Referer': (
                f"{self.base_url}/it618_award-award.html"
            ),
            'X-Requested-With': 'XMLHttpRequest',
        }

        try:
            response = self.session.get(
                url,
                headers=headers,
                timeout=20
            )

            try:
                res_json = response.json()
            except Exception:
                res_json = {}

            if res_json.get("tipname") == "":
                self.exchange_result = (
                    "无奖励（今日或已抽奖）"
                )

            elif res_json.get("tipname") == "ok":
                self.exchange_result = (
                    f"抽奖成功: "
                    f"{res_json.get('tipvalue')}"
                )

            else:
                self.exchange_result = (
                    f"非预期响应: "
                    f"{res_json.get('tipname')}"
                )

            self.exchange_logger.info(
                f"抽奖结果: {self.exchange_result}"
            )

        except Exception as e:
            self.exchange_result = f"异常: {e}"
            self.exchange_logger.error(
                f"抽奖异常: {e}"
            )

    def visit_spaces(self):
        uids = [730713, 62445, 61832]
        count = 0

        for uid in uids:
            try:
                self.session.get(
                    f"{self.base_url}/space-uid-{uid}.html",
                    timeout=15
                )
                count += 1
                time.sleep(1)
            except Exception:
                pass

        return count

    def poke_users(self):
        uids = [730713, 62445, 61832]
        count = 0

        for uid in uids:
            url = (
                f"{self.base_url}/home.php"
                f"?mod=spacecp"
                f"&ac=poke"
                f"&op=send"
                f"&uid={uid}"
                f"&inajax=1"
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
                    timeout=15
                )

                if "succeed" in response.text:
                    count += 1

                time.sleep(1)

            except Exception:
                pass

        return count

    def stance_blogs(self):
        count = 1
        page = 1

        while count < 10 and page <= 3:
            list_url = (
                f"{self.base_url}/home.php"
                f"?mod=space"
                f"&do=blog"
                f"&view=all"
                f"&catid=14"
                f"&page={page}"
            )

            try:
                response = self.session.get(
                    list_url,
                    timeout=20
                )

                blog_urls = set(
                    re.findall(
                        r'home\.php\?mod=space'
                        r'(?:&amp;|&)uid=\d+'
                        r'(?:&amp;|&)do=blog'
                        r'(?:&amp;|&)id=\d+',
                        response.text
                    )
                )

                for uri in blog_urls:
                    if count >= 10:
                        break

                    clean_uri = uri.replace('&amp;', '&')

                    blog_res = self.session.get(
                        f"{self.base_url}/{clean_uri}",
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
                            f"{self.base_url}/"
                            f"{click_match.group(1).replace('&amp;', '&')}"
                        )

                        click_res = self.session.get(
                            click_url,
                            headers={
                                'X-Requested-With':
                                    'XMLHttpRequest'
                            },
                            timeout=15
                        ).text

                        if "成功" in click_res:
                            count += 1

                    time.sleep(1)

            except Exception:
                break

            page += 1

        return count

    def draw_and_guess(self):
        url = (
            f"{self.base_url}/plugin.php"
            f"?id=viewui_draw"
            f"&mod=api"
            f"&ac=adddraw"
        )

        base64_img = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR4AWL6////fwAAAAD//w7I1cwAAAAGSURBVAMACgUD/9k79a8"
            "AAAAASUVORK5CYII="
        )

        data = {
            'title': '水果',
            'answer': '苹果',
            'pic': base64_img,
            'formhash': self.post_formhash
        }

        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': self.base_url,
            'Referer': (
                f"{self.base_url}/plugin.php?id=viewui_draw"
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
                    response.text[:100]
                )
            except Exception:
                msg = response.text[:100]

            self.task_logger.info(
                f"[Debug] 你画我猜真实返回: {msg}"
            )

            if "成功" in msg or "succeed" in msg:
                return "出题成功"

            if (
                "今日" in msg
                or "上限" in msg
                or "用完" in msg
            ):
                return "额度已满"

            return f"失败: {msg[:10]}"

        except Exception as e:
            self.task_logger.error(
                f"你画我猜提交异常: {e}"
            )
            return "提交异常"

    def fetch_assets(self):
        self.task_logger.info(
            "正在获取实时个人资产数据..."
        )

        url = (
            f"{self.base_url}/home.php"
            f"?mod=spacecp"
            f"&ac=credit"
            f"&op=base"
        )

        try:
            response = self.session.get(
                url,
                timeout=20
            )

            clean_text = re.sub(
                r'<[^>]+>',
                ' ',
                response.text
            )
            clean_text = re.sub(
                r'\s+',
                ' ',
                clean_text
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

                except Exception as e:
                    self.task_logger.warning(
                        f"读取金币记录失败: {e}"
                    )

            growth = current_gold - last_gold

            if growth >= 0:
                growth_str = f"+{growth}"
            else:
                growth_str = str(growth)

            with open(
                "gold_record.txt",
                "w",
                encoding="utf-8"
            ) as f:
                f.write(str(current_gold))

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
            f"当前账户综合看板:\n{self.assets_report}"
        )

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

    def send_notification(self):
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(
            os.getenv("SMTP_PORT", "465")
        )

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
            f"<p><b>核心签到:</b> {self.sign_result}</p>"
            f"<p><b>日常抽奖:</b> {self.exchange_result}</p>"
            f"<p><b>互动作业:</b> {self.task_result}</p>"
            "<br>"
            "<h4>📊 当前核心资产状态：</h4>"
            "<pre style='background:#f4f4f4;"
            "padding:15px;border-radius:5px;"
            "font-family:monospace;line-height:1.6;"
            "font-size:14px;'>"
            f"{self.assets_report}"
            "</pre>"
            "<br>"
            "<small style='color:#888;'>"
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
                Header("GM-Bot", 'utf-8').encode(),
                mail_user
            )
        )

        message['To'] = formataddr(
            (
                Header("Master", 'utf-8').encode(),
                mail_to
            )
        )

        message['Subject'] = Header(
            f"GameMale 任务运行报告 - {self.sign_result}",
            'utf-8'
        )

        try:
            server = smtplib.SMTP_SSL(
                smtp_host,
                smtp_port,
                timeout=20
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

    def run(self):
        self.main_logger.info(
            "=== GM-All-In-One 任务引擎启动 ==="
        )

        if not self.bypass_cloudflare():
            self.main_logger.error(
                "Chrome 浏览器会话建立失败，中止本次任务。"
            )
            return False

        if not self.login():
            return False

        self.sign_gamemale()
        self.daily_exchange()
        self.execute_interactive_tasks()
        self.fetch_assets()
        self.send_notification()

        self.main_logger.info(
            "=== 所有作业同步执行完毕 ==="
        )

        return True


if __name__ == "__main__":
    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")

    if not username or not password:
        print("缺少 USERNAME 或 PASSWORD")
        raise SystemExit(1)

    gm = Gamemale(
        username,
        password,
        verbose=False
    )

    success = gm.run()

    if not success:
        raise SystemExit(1)
