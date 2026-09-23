# -*- coding: utf-8 -*-
"""
get_user_cookie.py —— 一次性工具：取出你的登录 Cookie，并**当场验证它在 CI 上能不能用**

为什么必须这样做
----------------
站点装了 dev8133_cloudflare 插件（Turnstile 人机验证）。实测结论：

  * 爬虫 UA（Baiduspider 等）在**白名单**里 —— 不仅 GET，连 POST 都放行；
    唯一被封的是 misc.php（= 登录验证码图片的出处）。
  * 所以：**带上你自己的登录 Cookie + 爬虫 UA**，就能直接在 GitHub Runner 上跑，
    既不过 Turnstile、也不要验证码、连浏览器都不用开。
  * 反过来，靠浏览器在 CI 上过 Turnstile 是走不通的：GitHub Runner 是机房 IP，
    Turnstile 会一直停在 interaction_required（实测两轮全失败）。

这个脚本做两件事：
  1. 开一个 Chrome 让你正常登录，然后把 `TVj0_2132_*` 系列 Cookie 拼成一行；
  2. **立刻用爬虫 UA + 这行 Cookie 发一次真实 HTTP 请求**，确认服务端认这个登录态。
     第 2 步是关键 —— 没验过的 Cookie 塞进 Secret 等于赌博。

用法
----
    python get_user_cookie.py                # 打开浏览器 → 你登录 → 自动取串并验证
    python get_user_cookie.py --wait 900     # 最多等 15 分钟（默认 600 秒）
    python get_user_cookie.py --no-verify    # 只取串，不做 HTTP 验证

产出：
    ./user_cookie.txt        一行，可直接粘贴成 GitHub Secret `GM_USER_COOKIE`
    屏幕上的「指纹」请一起记下 —— CI 日志里会打印同一个指纹，对不上就说明 Secret 没生效。

Cookie 有效期约 30 天，过期后重跑一次即可（脚本会直接告诉你失效了）。
"""

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gm_gate import (  # noqa: E402
    DEFAULT_HOST,
    USER_COOKIE_ENV,
    GateKeeper,
    HttpEngine,
    cookie_fingerprint,
)

# 读页面里的 discuz_uid（0 = 游客）。注意 DrissionPage 要求 return 写在外层。
UID_JS = """
var m = document.documentElement.innerHTML.match(/discuz_uid\\s*=\\s*['"]?(\\d+)/);
return m ? m[1] : '0';
"""

AUTH_PREFIX = "TVj0_2132_"      # Discuz 的登录相关 Cookie 都带这个前缀
ALSO_KEEP = ("cf_clearance",)   # 有就一起带上（个别情况下能少走一次门）


def pick_cookies(all_cookies):
    """挑出登录相关的 Cookie。全都要也可以，但只带必要的更干净、更不容易被风控。"""
    picked = {k: v for k, v in all_cookies.items()
              if k.startswith(AUTH_PREFIX) or k in ALSO_KEEP}
    return picked or dict(all_cookies)


def mask(line, keep=28):
    """给一行 Cookie 做个脱敏预览，方便肉眼确认「粘的是这串」。"""
    if len(line) <= keep * 2:
        return line[:keep] + "..."
    return line[:keep] + " ... " + line[-12:]


def verify_over_http(line, logger):
    """
    真正有用的那一步：用蜘蛛 UA + 这行 Cookie 发请求，看服务端认不认。
    复用 gm_gate 里已经被实测验证过的代码路径，不另写一套逻辑。
    """
    os.environ[USER_COOKIE_ENV] = line
    http = HttpEngine(DEFAULT_HOST, logger)
    gate = GateKeeper(DEFAULT_HOST, logger, headless=True)
    try:
        ok = gate.try_user_cookie(http)
        return ok, gate.logged_uid, gate.user_cookie_state
    finally:
        gate.close()


def main():
    parser = argparse.ArgumentParser(description="提取并验证 GameMale 登录 Cookie")
    parser.add_argument("--wait", type=int, default=600, help="等待登录的秒数（默认 600）")
    parser.add_argument("--no-verify", action="store_true", help="跳过 HTTP 验证")
    parser.add_argument("--chrome", default=None, help="Chrome 可执行文件路径")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s",
                        datefmt="%H:%M:%S")
    logger = logging.getLogger("GetCookie")

    print("=" * 74)
    print("GameMale 登录 Cookie 提取 + 验证工具")
    print("=" * 74)
    print("即将打开 Chrome。请在窗口里完成登录（遇到人机验证就点一下）。")
    print("检测到登录态后会自动收尾，最多等 %d 秒。\n" % args.wait)

    gate = GateKeeper(DEFAULT_HOST, logger, headless=False, browser_path=args.chrome)
    if not gate.browser_path:
        print("[错误] 没找到 Chrome。请用 --chrome 指定 chrome.exe 路径。")
        return 1

    try:
        page = gate._launch_browser()
    except Exception as exc:  # noqa: BLE001
        print("[错误] 浏览器启动失败: %r" % exc)
        print("       排查：GM_CHROME_PATH / 关闭杀软拦截 / 不要用管理员身份运行")
        return 1

    try:
        try:
            page.get("https://%s/forum.php" % DEFAULT_HOST)
        except Exception as exc:  # noqa: BLE001
            print("[警告] 打开页面异常（可忽略，继续等）: %r" % exc)

        uid = "0"
        deadline = time.time() + args.wait
        last_tip = -1
        while time.time() < deadline:
            try:
                uid = str(page.run_js(UID_JS) or "0").strip()
            except Exception:  # noqa: BLE001
                uid = "0"
            if uid.isdigit() and int(uid) > 0:
                break
            left = int(deadline - time.time())
            if left // 30 != last_tip:
                last_tip = left // 30
                print("  ... 等待登录中（%d 秒后超时）。若一直停在人机验证，手动点一下复选框" % left)
            time.sleep(2)

        if not (uid.isdigit() and int(uid) > 0):
            print("\n[超时] 没检测到登录态。")
            print("  1) 确认已在弹出的 Chrome 里登录成功（右上角能看到你的用户名）")
            print("  2) 若卡在人机验证，手动点一下复选框，然后重跑本脚本")
            print("  3) 也可以在你自己日常用的浏览器里手动复制 Cookie（见 README）")
            return 1

        print("\n[成功] 已登录，uid = %s" % uid)
        cookies = gate._read_cookies(page)
        picked = pick_cookies(cookies)
        line = "; ".join("%s=%s" % (k, v) for k, v in picked.items())
        fp = cookie_fingerprint(line)

        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_cookie.txt")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(line + "\n")

        print("  Cookie 项数: %d（%s）"
              % (len(picked), ", ".join(sorted(picked))))
        print("  长度/指纹  : %d / %s" % (len(line), fp))
        print("  预览       : %s" % mask(line))
    finally:
        gate.close()

    # ---- 关键一步：当场验证这串在 CI 上能不能用 ----
    print("\n" + "-" * 74)
    if args.no_verify:
        print("[跳过] 未做 HTTP 验证（--no-verify）")
        verified = True
    else:
        print("正在用「爬虫 UA + 这行 Cookie」发真实请求验证...")
        try:
            ok, real_uid, state = verify_over_http(line, logger)
        except Exception as exc:  # noqa: BLE001
            print("[验证异常] %r" % exc)
            ok, real_uid, state = False, None, "error"
        verified = bool(ok)
        if ok:
            print("[验证通过] 服务端认这个登录态，uid=%s —— 这串可以直接用。" % real_uid)
        else:
            print("[验证未通过] 状态=%s" % state)
            print("  常见原因：只复制了部分 Cookie（缺 *_auth）、登录态其实没生效、")
            print("            或站点刚改过 Cookie 名。请重跑一次，或改用 DevTools 手动复制。")

    print("-" * 74)
    print("要存成 GitHub Secret：%s" % USER_COOKIE_ENV)
    print("=" * 74)
    print(line)
    print("=" * 74)
    print("指纹 %s —— 请记住它，CI 日志里会打印同一个值，可用来确认 Secret 是否生效。" % fp)
    print("也写到了：%s（.gitignore 已忽略，别提交）" % out)
    print("提示：约 30 天后失效，届时重跑本脚本。")

    return 0 if verified else 2


if __name__ == "__main__":
    sys.exit(main())
