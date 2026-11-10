import json
from collections.abc import Iterator
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

import chompjs
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from kudo_agent.research_core.email import EmailCollector
    from kudo_agent.research_core.models import Artifact


def _objects(value: object) -> Iterator[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


async def microsoft(collector: "EmailCollector"):
    bootstrap = await collector.fetch("https://login.live.com/")
    if result := collector.http_result(bootstrap):
        return result
    page = BeautifulSoup(collector.http.text(bootstrap), "html.parser")
    scripts = [tag.get_text() for tag in page.find_all("script")
               if tag.get_text().strip().startswith("var ServerData =")]
    if len(scripts) != 1:
        raise ValueError("Microsoft 登录页面缺少唯一的 ServerData 配置")
    config = chompjs.parse_js_object(scripts[0])
    endpoint = config["urlGetCredentialType"]
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.hostname != "login.live.com" or parsed.path != "/GetCredentialType.srf":
        raise ValueError("Microsoft 登录方式查询地址异常")
    token = BeautifulSoup(config["sFTTag"], "html.parser").select_one('input[name="PPFT"]')
    if token is None or not token.get("value"):
        raise ValueError("Microsoft 登录方式查询缺少流程令牌")
    response = await collector.fetch(
        endpoint, method="POST", follow_redirects=False,
        json_body={"username": collector.email, "flowToken": token["value"], "checkPhones": False,
                   "forceotclogin": False, "otclogindisallowed": True, "noPaOtcDisallowed": True,
                   "isOtherIdpSupported": True, "isRemoteNGCSupported": False, "isFidoSupported": False,
                   "isSignup": False, "isReactLoginRequest": True,
                   "uaid": config.get("sUnauthSessionID", "")},
        headers={"Origin": "https://login.live.com", "Referer": bootstrap.final_url},
    )
    return microsoft_result(collector, response)


def microsoft_result(collector: "EmailCollector", response: "Artifact"):
    if result := collector.http_result(response):
        return result
    payload = collector.json_object(response)
    code = payload.get("IfExistsResult")
    if type(code) is not int:
        raise ValueError("Microsoft 登录方式查询缺少整数 IfExistsResult")
    throttle = payload.get("ThrottleStatus", 0)
    if type(throttle) is not int or throttle not in {0, 1, 2}:
        raise ValueError("Microsoft 登录方式查询返回未知的 ThrottleStatus")
    attributes = {"result_code": code, "lookup_scope": "microsoft_identity", "throttle_status": throttle}
    if throttle:
        return collector.result("rate_limited", "部分 Microsoft 身份提供方受到限流，账户状态尚未完整确认",
                                response, **attributes)
    if code == 2:
        return collector.result("rate_limited", "登录方式查询受到限流，账户状态未知", response, **attributes)
    if code in {-1, 4}:
        return collector.result("unknown", "登录方式查询未确认账户状态", response, **attributes)
    if code == 1:
        return collector.result("not_detected", "登录方式接口返回未发现 Microsoft 身份记录", response, **attributes)
    meanings = {0: "当前 Microsoft 身份提供方", 5: "其他 Microsoft 身份提供方",
                6: "两个 Microsoft 身份提供方", 8: "Microsoft ACMA 身份提供方"}
    if code not in meanings:
        raise ValueError(f"Microsoft 返回未定义的登录方式查询状态：{code}")
    return collector.result("detected", f"登录方式接口返回存在记录（{meanings[code]}）", response, **attributes)


async def ebay(collector: "EmailCollector"):
    bootstrap = await collector.fetch("https://signin.ebay.com/signin")
    if result := collector.http_result(bootstrap):
        return result
    page = BeautifulSoup(collector.http.text(bootstrap), "html.parser")
    if (page.title and page.title.get_text(strip=True) == "Security Measure | eBay") or page.select_one("form#captcha_form"):
        return collector.result("blocked", "登录页面要求安全验证，邮箱尚未提交", bootstrap)
    scripts = [tag.get_text() for tag in page.find_all("script") if "csrfAjaxToken" in tag.get_text()]
    if len(scripts) != 1:
        raise ValueError("eBay 登录页面缺少唯一的邮箱检查配置")
    configs = [item for parsed in chompjs.parse_js_objects(scripts[0]) for item in _objects(parsed)
               if "csrfAjaxToken" in item and "defaultIdentifierServiceUrl" in item]
    if len(configs) != 1 or not configs[0]["csrfAjaxToken"]:
        raise ValueError("eBay 邮箱检查缺少有效的 CSRF 配置")
    config = configs[0]
    endpoint = urljoin(bootstrap.final_url, config["defaultIdentifierServiceUrl"])
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.hostname != "signin.ebay.com" or parsed.path != "/signin/srv/identifer":
        raise ValueError("eBay 邮箱检查地址异常")
    response = await collector.fetch(
        endpoint, method="POST", follow_redirects=False,
        form={"identifier": collector.email, "srt": config["csrfAjaxToken"], "webAuthNOptedInKeys": "",
              "webAuthNCredCheck": "false", "isIdentifierAutofilled": "false"},
        headers={"X-Requested-With": "XMLHttpRequest", "Origin": "https://signin.ebay.com",
                 "Referer": bootstrap.final_url},
    )
    location = urlsplit(response.headers.get("location", ""))
    if response.status_code in {301, 302, 303, 307, 308} and location.path == "/splashui/captcha":
        return collector.result("blocked", "邮箱检查要求浏览器安全验证，账户状态未知", response)
    if result := collector.http_result(response):
        return result
    payload = collector.json_object(response)
    if payload.get("signinErrorUpdate") is True and payload.get("regUrlErrorMsg") and payload.get("errorMsg"):
        return collector.result("not_detected", "标识符检查提示注册账户", response)
    if payload.get("userType") in {"regular", "2fa", "ppa", "ato", "eu_seller"} and not payload.get("errorMsg"):
        return collector.result("detected", "标识符检查返回可用的账户登录方式", response,
                                account_type=payload["userType"])
    if payload.get("errorMsg"):
        return collector.result("unknown", "标识符检查未确认账户状态", response)
    raise ValueError("eBay 邮箱检查返回未定义的账户状态结构")


async def adobe(collector: "EmailCollector"):
    response = await collector.fetch(
        "https://auth.services.adobe.com/signin/v2/users/accounts", method="POST", follow_redirects=False,
        json_body={"username": collector.email, "usernameType": "EMAIL"},
        headers={"X-IMS-CLIENTID": "SunbreakWebUI1", "Origin": "https://auth.services.adobe.com"},
    )
    if result := collector.http_result(response):
        return result
    accounts = json.loads(collector.http.text(response))
    if not isinstance(accounts, list):
        raise ValueError("Adobe 账户类型查询必须返回 JSON 数组")
    if not accounts:
        return collector.result("not_detected", "账户类型接口未返回邮箱关联账户", response)
    if any(not isinstance(account, dict) or not isinstance(account.get("type"), str)
           or not isinstance(account.get("authenticationMethods"), list) for account in accounts):
        raise ValueError("Adobe 账户类型查询返回无效的账户记录")
    types = sorted({account["type"] for account in accounts})
    if any(account["type"] in {"individual", "enterprise"} and account["authenticationMethods"]
           for account in accounts):
        return collector.result("detected", "账户类型接口返回邮箱关联的登录方式", response,
                                account_types=types)
    return collector.result("unknown", "接口返回联合身份或组织登录配置，邮箱注册状态待确认", response,
                            account_types=types)


HANDLERS = {"microsoft": microsoft, "ebay": ebay, "adobe": adobe}
