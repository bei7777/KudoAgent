from urllib.parse import urljoin, urlsplit

import chompjs
import tree_sitter_javascript
from bs4 import BeautifulSoup
from tree_sitter import Language, Parser, Query, QueryCursor


def soundcloud_csrf(source: str) -> str:
    language = Language(tree_sitter_javascript.language())
    tree = Parser(language).parse(source.encode("utf-8"))
    if tree.root_node.has_error:
        raise ValueError("SoundCloud 登录脚本无法通过 JavaScript 语法解析")
    query = Query(language, """
        ((object (pair key: (string (string_fragment) @name) value: (string))) @headers
         (#eq? @name "X-Csrf-Token"))
    """)
    objects = QueryCursor(query).captures(tree.root_node).get("headers", [])
    tokens = {chompjs.parse_js_object(node.text.decode("utf-8"))["X-Csrf-Token"] for node in objects}
    if len(tokens) != 1:
        raise ValueError("SoundCloud 登录脚本缺少唯一的 CSRF 请求头配置")
    token = tokens.pop()
    if not isinstance(token, str) or not token or not token.isascii() or not token.isprintable():
        raise ValueError("SoundCloud 登录脚本的 CSRF 请求头配置无效")
    return token


async def twitter(collector):
    response = await collector.fetch("https://api.x.com/i/users/email_available.json",
                                     params={"email": collector.email},
                                     headers={"Accept": "application/json"})
    if result := collector.http_result(response):
        return result
    payload = collector.json_object(response)
    if type(payload.get("taken")) is not bool or type(payload.get("valid")) is not bool:
        raise ValueError("X 邮箱校验缺少明确的 taken 与 valid 状态")
    if payload["taken"]:
        return collector.result("detected", "邮箱字段检查返回已占用", response)
    if payload["valid"]:
        return collector.result("not_detected", "邮箱字段检查返回可用", response)
    return collector.result("unknown", "邮箱字段未通过校验，账户状态未知", response)


async def soundcloud(collector):
    bootstrap = await collector.fetch("https://soundcloud.com/signin")
    if result := collector.http_result(bootstrap):
        return result
    page = BeautifulSoup(collector.http.text(bootstrap), "html.parser")
    hydration = next((script for script in page.find_all("script", src=False)
                      if "window.__sc_hydration" in script.get_text()), None)
    if hydration is None:
        raise ValueError("SoundCloud 登录页面缺少客户端配置")
    data = chompjs.parse_js_object(hydration.get_text())
    if not isinstance(data, list):
        raise ValueError("SoundCloud 客户端配置必须为数组")
    client = next((item.get("data") for item in data
                   if isinstance(item, dict) and item.get("hydratable") == "apiClient"), None)
    if not isinstance(client, dict) or not isinstance(client.get("id"), str) or not client["id"]:
        raise ValueError("SoundCloud 登录页面缺少有效的公共客户端标识")
    auth = await collector.fetch("https://secure.soundcloud.com/web-auth")
    if result := collector.http_result(auth):
        return result
    auth_page = BeautifulSoup(collector.http.text(auth), "html.parser")
    scripts = [urljoin(auth.final_url, script["src"]) for script in auth_page.select("script[src]")]
    bundles = [url for url in scripts if urlsplit(url).hostname == "secure.sndcdn.com"
               and urlsplit(url).path.startswith("/web_auth-")]
    if len(bundles) != 1:
        raise ValueError("SoundCloud 匿名登录页面缺少唯一的官方登录脚本")
    bundle = await collector.fetch(bundles[0])
    if bundle.status_code != 200:
        if result := collector.http_result(bundle):
            return result
        raise ValueError(f"SoundCloud 登录脚本返回 HTTP {bundle.status_code}")
    csrf = soundcloud_csrf(collector.http.text(bundle))
    response = await collector.fetch("https://api-auth.soundcloud.com/web-auth/identifier",
                                     params={"q": collector.email, "client_id": client["id"]},
                                     headers={"Accept": "application/json",
                                              "Origin": "https://secure.soundcloud.com",
                                              "Referer": auth.final_url, "X-Csrf-Token": csrf})
    if result := collector.http_result(response):
        return result
    payload = collector.json_object(response)
    if payload.get("type") != "email":
        return collector.result("unknown", "标识符检查未返回邮箱判定", response)
    state = payload.get("status")
    if state not in {"available", "in_use"}:
        return collector.result("unknown", "邮箱检查没有返回明确的可用性状态", response)
    return collector.result("detected" if state == "in_use" else "not_detected",
                            "标识符接口返回邮箱已使用" if state == "in_use" else "标识符接口返回邮箱可用",
                            response, identifier_status=state, sign_in_option=payload.get("sign_in_option"))


async def flickr(collector):
    response = await collector.fetch("https://identity-api.flickr.com/migration",
                                     params={"email": collector.email},
                                     headers={"Accept": "application/json",
                                              "Origin": "https://identity.flickr.com",
                                              "Referer": "https://identity.flickr.com/login"})
    if result := collector.http_result(response):
        return result
    payload = collector.json_object(response)
    if payload.get("stat") == "ok":
        code = payload.get("state_code")
        if type(code) not in {int, str} or not str(code).isdigit():
            raise ValueError("Flickr 登录检查缺少有效的迁移状态")
        next_step = "password_login" if int(code) > 0 else "account_update"
        return collector.result("metadata", "登录流程返回密码登录或账户更新入口，注册状态尚未确认", response,
                                migration_state=code, next_step=next_step, registration_status="unknown")
    if payload.get("stat") == "fail":
        code = payload.get("code")
        if code in {1, 25}:
            return collector.result("unknown", "登录流程导向注册入口，注册状态尚未确认", response,
                                    result_code=code, next_step="signup", registration_status="unknown")
        return collector.result("unknown", "登录方式检查未取得明确结果", response, result_code=code)
    raise ValueError("Flickr 登录检查缺少有效的 stat 状态")


async def plurk(collector):
    response = await collector.fetch("https://www.plurk.com/v2/account/validation/email", method="POST",
                                     form={"email": collector.email}, headers={"Accept": "application/json"})
    if result := collector.http_result(response):
        return result
    state = collector.json_object(response).get("status")
    if state == "exist":
        return collector.result("detected", "邮箱字段检查返回已经注册", response, result_status=state)
    if state == "unused":
        return collector.result("not_detected", "邮箱字段检查返回尚未使用", response, result_status=state)
    if state == "blocked":
        return collector.result("blocked", "平台限制使用该邮箱，注册状态未知", response, result_status=state)
    if state == "invalid":
        return collector.result("unknown", "邮箱未通过平台字段校验，注册状态未知", response, result_status=state)
    raise ValueError("Plurk 邮箱校验返回未知的字段状态")


async def komoot(collector):
    response = await collector.fetch("https://www.komoot.com/v1/signin", method="POST",
                                     json_body={"email": collector.email},
                                     headers={"Accept": "application/json"})
    if result := collector.http_result(response):
        return result
    payload = collector.json_object(response)
    flow = payload.get("type")
    if payload.get("error"):
        return collector.result("unknown", "邮箱首步检查返回业务限制，账户状态未知", response,
                                next_step=flow, result_code=payload["error"])
    if flow == "login":
        return collector.result("detected", "邮箱首步检查选择已有账户登录表单", response, next_step=flow)
    if flow == "register":
        return collector.result("not_detected", "邮箱首步检查选择新账户注册表单", response, next_step=flow)
    return collector.result("unknown", "邮箱首步检查未返回明确的登录或注册状态", response,
                            next_step=flow, result_code=payload.get("error"))


HANDLERS = {"twitter": twitter, "soundcloud": soundcloud, "flickr": flickr,
            "plurk": plurk, "komoot": komoot}
