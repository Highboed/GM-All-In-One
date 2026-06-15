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
    def __init__(self, username, password, questionid='0', answer=None, verbose=False):
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
        self.session = requests.session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/91.0.4472.124 Safari/537.36'
            )
        })

    def get_login_formhash(self):
        url = f"https://{self.hostname}/member.php?mod=logging&action=login"
        text = self.session.get(url).text
        loginhash_match = re.search(r'<div id="main_messaqge_(.+?)">', text)
        formhash_match = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', text)
        if not loginhash_match or not formhash_match:
            raise ValueError("无法获取 loginhash 或 formhash")
        return loginhash_match.group(1), formhash_match.group(1)

    def verify_code(self, max_retries=10) -> str:
        self.login_logger.info(f"正在识别验证码 [最大重试次数: {max_retries}]")
        for attempt in range(1, max_retries + 1):
            update_url = f"https://{self.hostname}/misc.php?mod=seccode&action=update&idhash=cSA&0.1234567&modid=member::logging"
            update_text = self.session.get(update_url).text
            update_match = re.search(r"update=(.+?)&idhash=", update_text)
            if not update_match:
                continue
            code_url = f"https://{self.hostname}/misc.php?mod=seccode&update={update_match.group(1)}&idhash=cSA"
            headers = {
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Referer': f"https://{self.hostname}/member.php?mod=logging&action=login",
            }
            code_resp = self.session.get(code_url, headers=headers)
            if not code_resp.content:
                continue
            code = self.ocr.classification(code_resp.content)
            verify_url = f"https://{self.hostname}/misc.php?mod=seccode&action=check&inajax=1&modid=member::logging&idhash=cSA&secverify={code}"
            if "succeed" in self.session.get(verify_url).text:
                self.login_logger.info(f"验证码识别成功: {code} (尝试第 {attempt} 次)")
                return code
        return ""

    def login(self) -> bool:
        self.login_logger.info("开始登录流程...")
        code = self.verify_code()
        if not code:
            self.login_logger.error("验证码识别失败，中止登录")
            return False
        loginhash, formhash = self.get_login_formhash()
        login_url = f"https://{self.hostname}/member.php?mod=logging&action=login&loginsubmit=yes&loginhash={loginhash}&inajax=1"
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
        resp_text = self.session.post(login_url, data=form_data).text
        if "succeed" in resp_text:
            self.login_logger.info("登录成功")
            try:
                text = self.session.get(f"https://{self.hostname}/forum.php").text
                formhash_match = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', text)
                if formhash_match:
                    self.post_formhash = formhash_match.group(1)
            except Exception as e:
                self.login_logger.error(f"提取全局 formhash 失败: {e}")
            return True
        else:
            self.login_logger.error("登录失败，请检查凭证或安全提问设置")
            return False

    def sign_gamemale(self):
        self.sign_logger.info("执行每日签到...")
        if not self.post_formhash:
            self.sign_result = "失败：缺少 formhash"
            return
        url = f"https://{self.hostname}/k_misign-sign.html?operation=qiandao&format=button&formhash={self.post_formhash}"
        try:
            res = self.session.get(url).text
            if "签到成功" in res:
                self.sign_result = "签到成功"
            elif "已签" in res:
                self.sign_result = "今日已签到"
            else:
                self.sign_result = "未知响应状态"
            self.sign_logger.info(f"签到结果: {self.sign_result}")
        except Exception as e:
            self.sign_result = f"异常: {e}"

    def daily_exchange(self):
        self.exchange_logger.info("执行日常卡片抽奖...")
        if not self.post_formhash:
            self.exchange_result = "失败：缺少 formhash"
            return
        url = f"https://{self.hostname}/plugin.php?id=it618_award:ajax&ac=getaward&formhash={self.post_formhash}&_={str(int(time.time() * 1000))}"
        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'referer': f"https://{self.hostname}/it618_award-award.html",
            'x-requested-with': 'XMLHttpRequest',
        }
        try:
            res_json = self.session.get(url, headers=headers).json()
            if res_json.get("tipname") == "":
                self.exchange_result = "无奖励（今日或已抽奖）"
            elif res_json.get("tipname") == "ok":
                self.exchange_result = f"抽奖成功: {res_json.get('tipvalue')}"
            else:
                self.exchange_result = f"非预期响应: {res_json.get('tipname')}"
            self.exchange_logger.info(f"抽奖结果: {self.exchange_result}")
        except Exception as e:
            self.exchange_result = f"异常: {e}"

    def visit_spaces(self):
        uids = [730713, 62445, 61832]
        count = 0
        for uid in uids:
            try:
                self.session.get(f"https://{self.hostname}/space-uid-{uid}.html")
                count += 1
                time.sleep(1)
            except:
                pass
        return count

    def poke_users(self):
        uids = [730713, 62445, 61832]
        count = 0
        for uid in uids:
            url = f"https://{self.hostname}/home.php?mod=spacecp&ac=poke&op=send&uid={uid}&inajax=1"
            data = {'formhash': self.post_formhash, 'poke': '1', 'iconid': '3', 'pokesubmit': 'true'}
            try:
                if "succeed" in self.session.post(url, data=data).text:
                    count += 1
                time.sleep(1)
            except:
                pass
        return count

    def stance_blogs(self):
        count, page = 0, 1
        while count < 10 and page <= 3:
            list_url = f"https://{self.hostname}/home.php?mod=space&do=blog&view=all&catid=14&page={page}"
            try:
                res = self.session.get(list_url).text
                blog_urls = set(re.findall(r'home\.php\?mod=space(?:&amp;|&)uid=\d+(?:&amp;|&)do=blog(?:&amp;|&)id=\d+', res))
                for uri in blog_urls:
                    if count >= 10: break
                    blog_res = self.session.get(f"https://{self.hostname}/{uri.replace('&amp;', '&')}").text
                    click_match = re.search(r'(home\.php\?mod=spacecp(?:&amp;|&)ac=click(?:&amp;|&)op=add[^"\']+)', blog_res)
                    if click_match:
                        click_url = f"https://{self.hostname}/{click_match.group(1).replace('&amp;', '&')}"
                        if "成功" in self.session.get(click_url, headers={'x-requested-with': 'XMLHttpRequest'}).text:
                            count += 1
                    time.sleep(1)
            except:
                break
            page += 1
        return count

    def draw_and_guess(self):
        url = f"https://{self.hostname}/plugin.php?id=viewui_draw&mod=api&ac=adddraw"
        base64_img = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADklEQVR4AWL6////fwAAAAD//w7I1cwAAAAGSURBVAMACgUD/9k79a8AAAAASUVORK5CYII="
        data = {'title': '水果', 'answer': '苹果', 'pic': base64_img}
        headers = {
            'x-requested-with': 'XMLHttpRequest', 
            'referer': f"https://{self.hostname}/plugin.php?id=viewui_draw&mod=list&ac=draw"
        }
        try:
            res = self.session.post(url, data=data, headers=headers).text
            self.task_logger.info(f"[Debug] 你画我猜服务器返回: {res[:50]}")
            
            if "成功" in res or "succeed" in res or "200" in res:
                return "出题成功"
            elif "今日" in res or "上限" in res or "次" in res:
                return "额度已满"
            else:
                return f"失败: {res[:10]}"
        except Exception as e:
            return "提交异常"

    def fetch_assets(self):
        self.task_logger.info("正在获取实时个人资产数据...")
        credit_url = f"https://{self.hostname}/home.php?mod=spacecp&ac=credit&op=base"
        forum_url = f"https://{self.hostname}/forum.php"
        try:
            credit_res = self.session.get(credit_url).text
            forum_res = self.session.get(forum_url).text
            
            gold_match = re.search(r'金币\s*[:：]\s*(\d+)', forum_res) or re.search(r'金币\s*[:：]\s*(\d+)', credit_res)
            current_gold = int(gold_match.group(1)) if gold_match else 0
            
            last_gold = current_gold
            if os.path.exists("gold_record.txt"):
                with open("gold_record.txt", "r") as f:
                    content = f.read().strip()
                    if content.isdigit():
                        last_gold = int(content)
            
            growth = current_gold - last_gold
            growth_str = f"+{growth}" if growth >= 0 else str(growth)
            
            with open("gold_record.txt", "w") as f:
                f.write(str(current_gold))
                
            self.assets_report = f"- 金币: {current_gold} (较昨日: {growth_str})"
            
            clean_text = re.sub(r'<[^>]+>', '', credit_res)
            matches = re.findall(r'(血液|旅程|追随|知识|咒术|堕落|灵魂)\s*[:：]?\s*(\d+)', clean_text)
            if matches:
                self.assets_report += "\n" + "\n".join([f"- {k}: {v}" for k, v in matches])
        except Exception as e:
            self.assets_report = f"资产抓取异常: {e}"
            
        self.task_logger.info(f"当前账户资产状况:\n{self.assets_report}")

    def execute_interactive_tasks(self):
        self.task_logger.info("开始执行互动作业...")
        s_count = self.visit_spaces()
        p_count = self.poke_users()
        b_count = self.stance_blogs()
        d_status = self.draw_and_guess()
        self.task_result = f"空间访问({s_count}/3) | 打招呼({p_count}/3) | 日志表态({b_count}/10) | 你画我猜({d_status})"
        self.task_logger.info(f"互动作业结果: {self.task_result}")

    def send_notification(self):
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = os.getenv("SMTP_PORT", "465")
        mail_user = os.getenv("MAIL_USER")
        mail_pass = os.getenv("MAIL_PASS")
        mail_to = os.getenv("MAIL_TO", mail_user)
        
        if not all([smtp_host, mail_user, mail_pass]):
            self.notice_logger.warning("未配置完整的 SMTP 环境变量，跳过邮件通知流程")
            return
            
        self.notice_logger.info("正在发送推送邮件...")
        mail_content = (
            f"<h3>GameMale 每日自动化任务报告</h3>"
            f"<p><b>核心签到:</b> {self.sign_result}</p>"
            f"<p><b>日常抽奖:</b> {self.exchange_result}</p>"
            f"<p><b>互动作业:</b> {self.task_result}</p>"
            f"<br><h4>📊 当前实时资产状态：</h4>"
            f"<pre style='background:#f4f4f4;padding:10px;border-radius:5px;font-family:monospace;'>{self.assets_report}</pre>"
            f"<br><small style='color:#888;'>报告由 GM-All-In-One 自动化引擎生成</small>"
        )
        
        message = MIMEText(mail_content, 'html', 'utf-8')
        
        # [修复重点] 使用 formataddr 规范发送人和接收人头部格式，符合 RFC 协议要求
        message['From'] = formataddr((Header("GM-Bot", 'utf-8').encode(), mail_user))
        message['To'] = formataddr((Header("Master", 'utf-8').encode(), mail_to))
        message['Subject'] = Header(f"GameMale 任务运行报告 - {self.sign_result}", 'utf-8')
        
        try:
            server = smtplib.SMTP_SSL(smtp_host, int(smtp_port))
            server.login(mail_user, mail_pass)
            server.sendmail(mail_user, [mail_to], message.as_string())
            server.quit()
            self.notice_logger.info("推送邮件发送成功")
        except Exception as e:
            self.notice_logger.error(f"推送邮件发送失败: {e}")

    def run(self):
        self.main_logger.info("=== GM-All-In-One 任务引擎启动 ===")
        if not self.login():
            return
        self.sign_gamemale()
        self.daily_exchange()
        self.execute_interactive_tasks()
        self.fetch_assets()
        self.send_notification()
        self.main_logger.info("=== 所有作业同步执行完毕 ===")

if __name__ == "__main__":
    username = os.getenv("USERNAME")
    password = os.getenv("PASSWORD")
    if not username or not password:
        exit(1)
    gm = Gamemale(username, password, verbose=False)
    gm.run()
