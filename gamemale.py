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
                'Mozilla/5.0 (X11; Linux x86_64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        })

    # =========================================================
    # Cloudflare / Chrome
    # =========================================================

    def bypass_cloudflare(self):
        """
        GitHub Actions 环境中：
        1. 连接已经由 workflow 启动的 Chrome
        2. 访问 GameMale
        3. 等待 Cloudflare
        4. 获取 Cookie 和 User-Agent
        5. 转移给 requests.Session
        """

        self.main_logger.info(
            "连接 GitHub Actions 中已经启动的 Chrome，准备处理 Cloudflare..."
        )

        browser_address = os.getenv(
            "DRISSIONPAGE_ADDRESS",
            "127.0.0.1:9222"
        )

        try:
            co = ChromiumOptions()

            # 不让 DrissionPage 再自行启动 Chrome
            co.set_local_port(9222)

            self.main_logger.info(
                f"尝试连接 Chrome 调试端口: {browser_address}"
            )

            page = ChromiumPage(co)

            try:
                url = f"https://{self.hostname}/forum.php"

                self.main_logger.info(
                    f"访问 GameMale: {url}"
                )

                page.get(url)

                self.main_logger.info(
                    "正在等待 Cloudflare 验证..."
                )

                # 给 Cloudflare 足够时间
                for i in range(30):
                    try:
                        title = page.title

                        self.main_logger.info(
                            f"Cloudflare 检测中... [{i + 1}/30] 当前标题: {title}"
                        )

                        if title and "Just a moment" not in title:
                            break

                    except Exception:
                        pass

                    time.sleep(1)

                # 最终再等待几秒，让 Cookie 完成写入
                time.sleep(5)

                # =====================================================
                # 获取 Cookie
                # =====================================================

                cookies = page.cookies(as_dict=True)

                if not cookies:
                    raise RuntimeError(
                        "Chrome 没有返回 Cookie，Cloudflare 会话可能没有建立成功。"
                    )

                ua = page.user_agent

                self.session.cookies.update(cookies)

                if ua:
                    self.session.headers.update({
                        'User-Agent': ua
                    })

                self.main_logger.info(
                    f"Chrome Cookie 获取成功，共 {len(cookies)} 个 Cookie。"
                )

                self.main_logger.info(
                    "🔥 Cloudflare 浏览器会话建立成功，已接管 requests 会话。"
                )

                return True

            finally:
                try:
                    page.quit()
                except Exception:
                    pass

        except Exception as e:
            self.main_logger.error(
                f"Chrome / Cloudflare 处理异常: {e}"
            )

            return False

    # =========================================================
    # Login
    # =========================================================

    def get_login_formhash(self):

        url = (
            f"https://{self.hostname}/"
            f"member.php?mod=logging&action=login"
        )

        response = self.session.get(
            url,
            timeout=30
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

    def verify_code(self, max_retries=10):

        self.login_logger.info(
            f"正在识别验证码 [最大重试次数: {max_retries}]"
        )

        for attempt in range(1, max_retries + 1):

            try:

                update_url = (
                    f"https://{self.hostname}/"
                    f"misc.php?mod=seccode"
                    f"&action=update"
                    f"&idhash=cSA"
                    f"&0.1234567"
                    f"&modid=member::logging"
                )

                update_text = self.session.get(
                    update_url,
                    timeout=30
                ).text

                update_match = re.search(
                    r'update=(.+?)&idhash=',
                    update_text
                )

                if not update_match:
                    self.login_logger.warning(
                        f"第 {attempt} 次获取验证码参数失败"
                    )
                    continue

                code_url = (
                    f"https://{self.hostname}/"
                    f"misc.php?mod=seccode"
                    f"&update={update_match.group(1)}"
                    f"&idhash=cSA"
                )

                headers = {
                    'Accept': (
                        'image/webp,image/apng,image/*,'
                        '*/*;q=0.8'
                    ),
                    'Referer': (
                        f"https://{self.hostname}/"
                        f"member.php?mod=logging&action=login"
                    )
                }

                code_resp = self.session.get(
                    code_url,
                    headers=headers,
                    timeout=30
                )

                if not code_resp.content:
                    continue

                code = self.ocr.classification(
                    code_resp.content
                )

                if not code:
                    continue

                verify_url = (
                    f"https://{self.hostname}/"
                    f"misc.php?mod=seccode"
                    f"&action=check"
                    f"&inajax=1"
                    f"&modid=member::logging"
                    f"&idhash=cSA"
                    f"&secverify={code}"
                )

                verify_resp = self.session.get(
                    verify_url,
                    timeout=30
                )

                if "succeed" in verify_resp.text:

                    self.login_logger.info(
                        f"验证码识别成功: {code} "
                        f"(尝试第 {attempt} 次)"
                    )

                    return code

            except Exception as e:

                self.login_logger.warning(
                    f"第 {attempt} 次验证码处理异常: {e}"
                )

            time.sleep(1)

        return ""

    def login(self):

        self.login_logger.info(
            "开始登录流程..."
        )

        try:

            code = self.verify_code()

            if not code:

                self.login_logger.error(
                    "验证码识别失败，中止登录"
                )

                return False

            loginhash, formhash = self.get_login_formhash()

            login_url = (
                f"https://{self.hostname}/"
                f"member.php?mod=logging"
                f"&action=login"
                f"&loginsubmit=yes"
                f"&loginhash={loginhash}"
                f"&inajax=1"
            )

            form_data = {

                'formhash': formhash,

                'referer':
                    f"https://{self.hostname}/",

                'loginfield':
                    self.username,

                'username':
                    self.username,

                'password':
                    self.password,

                'questionid':
                    self.questionid,

                'answer':
                    self.answer,

                'cookietime':
                    2592000,

                'seccodehash':
                    'cSA',

                'seccodemodid':
                    'member::logging',

                'seccodeverify':
                    code,
            }

            response = self.session.post(
                login_url,
                data=form_data,
                timeout=30
            )

            resp_text = response.text

            if "succeed" in resp_text:

                self.login_logger.info(
                    "登录成功"
                )

                try:

                    text = self.session.get(
                        f"https://{self.hostname}/forum.php",
                        timeout=30
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
                            "登录成功，但没有找到全局 formhash"
                        )

                except Exception as e:

                    self.login_logger.error(
                        f"提取全局 formhash 失败: {e}"
                    )

                return True

            self.login_logger.error(
                "登录失败，请检查用户名、密码或安全提问设置"
            )

            return False

        except Exception as e:

            self.login_logger.error(
                f"登录流程异常: {e}"
            )

            return False

    # =========================================================
    # 每日签到
    # =========================================================

    def sign_gamemale(self):

        self.sign_logger.info(
            "执行每日签到..."
        )

        if not self.post_formhash:

            self.sign_result = "失败：缺少 formhash"

            return

        url = (
            f"https://{self.hostname}/"
            f"k_misign-sign.html"
            f"?operation=qiandao"
            f"&format=button"
            f"&formhash={self.post_formhash}"
        )

        try:

            res = self.session.get(
                url,
                timeout=30
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

    # =========================================================
    # 抽奖
    # =========================================================

    def daily_exchange(self):

        self.exchange_logger.info(
            "执行日常卡片抽奖..."
        )

        if not self.post_formhash:

            self.exchange_result = "失败：缺少 formhash"

            return

        url = (
            f"https://{self.hostname}/"
            f"plugin.php?id=it618_award:ajax"
            f"&ac=getaward"
            f"&formhash={self.post_formhash}"
            f"&_={int(time.time() * 1000)}"
        )

        headers = {

            'accept':
                'application/json, text/javascript, */*; q=0.01',

            'referer':
                f"https://{self.hostname}/it618_award-award.html",

            'x-requested-with':
                'XMLHttpRequest',
        }

        try:

            response = self.session.get(
                url,
                headers=headers,
                timeout=30
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

    # =========================================================
    # 访问空间
    # =========================================================

    def visit_spaces(self):

        uids = [
            730713,
            62445,
            61832
        ]

        count = 0

        for uid in uids:

            try:

                self.session.get(
                    f"https://{self.hostname}/space-uid-{uid}.html",
                    timeout=30
                )

                count += 1

                time.sleep(1)

            except Exception:

                pass

        return count

    # =========================================================
    # 打招呼
    # =========================================================

    def poke_users(self):

        uids = [
            730713,
            62445,
            61832
        ]

        count = 0

        for uid in uids:

            url = (
                f"https://{self.hostname}/"
                f"home.php?mod=spacecp"
                f"&ac=poke"
                f"&op=send"
                f"&uid={uid}"
                f"&inajax=1"
            )

            data = {

                'formhash':
                    self.post_formhash,

                'poke':
                    '1',

                'iconid':
                    '3',

                'pokesubmit':
                    'true'
            }

            try:

                response = self.session.post(
                    url,
                    data=data,
                    timeout=30
                )

                if "succeed" in response.text:

                    count += 1

                time.sleep(1)

            except Exception:

                pass

        return count

    # =========================================================
    # 日志表态
    # =========================================================

    def stance_blogs(self):

        count = 1
        page = 1

        while count < 10 and page <= 3:

            list_url = (
                f"https://{self.hostname}/"
                f"home.php?mod=space"
                f"&do=blog"
                f"&view=all"
                f"&catid=14"
                f"&page={page}"
            )

            try:

                res = self.session.get(
                    list_url,
                    timeout=30
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

                    uri = uri.replace(
                        '&amp;',
                        '&'
                    )

                    blog_url = (
                        f"https://{self.hostname}/{uri}"
                    )

                    blog_res = self.session.get(
                        blog_url,
                        timeout=30
                    ).text

                    click_match = re.search(
                        r'(home\.php\?mod=spacecp'
                        r'(?:&amp;|&)ac=click'
                        r'(?:&amp;|&)op=add[^"\']+)',
                        blog_res
                    )

                    if click_match:

                        click_url = click_match.group(1).replace(
                            '&amp;',
                            '&'
                        )

                        click_url = (
                            f"https://{self.hostname}/{click_url}"
                        )

                        click_res = self.session.get(
                            click_url,
                            headers={
                                'x-requested-with':
                                    'XMLHttpRequest'
                            },
                            timeout=30
                        ).text

                        if "成功" in click_res:

                            count += 1

                    time.sleep(1)

            except Exception:

                break

            page += 1

        return count

    # =========================================================
    # 你画我猜
    # =========================================================

    def draw_and_guess(self):

        url = (
            f"https://{self.hostname}/"
            f"plugin.php?id=viewui_draw"
            f"&mod=api"
            f"&ac=adddraw"
        )

        base64_img = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADklEQVR4AWL6////fwAAAAD//w7I1cwAAAAGSURBVAMACgUD"
            "/9k79a8AAAAASUVORK5CYII="
        )

        data = {

            'title':
                '水果',

            'answer':
                '苹果',

            'pic':
                base64_img,

            'formhash':
                self.post_formhash
        }

        headers = {

            'x-requested-with':
                'XMLHttpRequest',

            'origin':
                f"https://{self.hostname}",

            'referer':
                f"https://{self.hostname}/plugin.php?id=viewui_draw"
        }

        try:

            response = self.session.post(
                url,
                data=data,
                headers=headers,
                timeout=30
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

        except Exception:

            return "提交异常"

    # =========================================================
    # 获取资产
    # =========================================================

    def fetch_assets(self):

        self.task_logger.info(
            "正在获取实时个人资产数据..."
        )

        url = (
            f"https://{self.hostname}/"
            f"home.php?mod=spacecp"
            f"&ac=credit"
            f"&op=base"
        )

        try:

            response = self.session.get(
                url,
                timeout=30
            )

            res = response.text

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
                    rf'{re.escape(item)}\s*[:：]?\s*(\d+)',
                    clean_text
                )

                assets_dict[item] = (
                    int(match.group(1))
                    if match
                    else 0
                )

            current_gold = assets_dict['金币']

            last_gold = current_gold

            if os.path.exists(
                "gold_record.txt"
            ):

                with open(
                    "gold_record.txt",
                    "r",
                    encoding="utf-8"
                ) as f:

                    content = f.read().strip()

                    if content.isdigit():

                        last_gold = int(content)

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

    # =========================================================
    # 互动作业
    # =========================================================

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

    # =========================================================
    # 邮件通知
    # =========================================================

    def send_notification(self):

        smtp_host = os.getenv(
            "SMTP_HOST"
        )

        smtp_port = 465

        mail_user = os.getenv(
            "MAIL_USER"
        )

        mail_pass = os.getenv(
            "MAIL_PASS"
        )

        mail_to = os.getenv(
            "MAIL_TO"
        )

        if (
            not mail_to
            or mail_to.strip() == ""
        ):

            mail_to = mail_user

        if not all([
            smtp_host,
            mail_user,
            mail_pass
        ]):

            self.notice_logger.warning(
                "未配置完整的 SMTP_HOST、"
                "发件人邮箱或授权码，"
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

            "<br>"
            "<h4>📊 当前核心资产状态：</h4>"

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

    # =========================================================
    # 主流程
    # =========================================================

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

            self.main_logger.error(
                "登录失败，任务终止。"
            )

            return

        # 签到
        self.sign_gamemale()

        # 抽奖
        self.daily_exchange()

        # 日常任务
        self.execute_interactive_tasks()

        # 资产
        self.fetch_assets()

        # 邮件
        self.send_notification()

        self.main_logger.info(
            "=== 所有作业同步执行完毕 ==="
        )


if __name__ == "__main__":

    username = os.getenv(
        "USERNAME"
    )

    password = os.getenv(
        "PASSWORD"
    )

    if not username or not password:

        print(
            "ERROR: USERNAME 或 PASSWORD 未配置"
        )

        exit(1)

    gm = Gamemale(
        username,
        password,
        verbose=False
    )

    gm.run()
