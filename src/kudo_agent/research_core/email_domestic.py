from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

import esprima
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from kudo_agent.research_core.email import EmailCollector, EmailObservation


async def huawei_cloud(collector: "EmailCollector") -> "EmailObservation":
    bootstrap = await collector.fetch("https://reg.huaweicloud.com/registerui/cn/index.html")
    if result := collector.http_result(bootstrap):
        return result
    page = BeautifulSoup(collector.http.text(bootstrap), "html.parser")
    meta = page.select_one("#hwmeta")
    cookies = collector.http.clients[collector.scope].cookies
    headers = {"Referer": bootstrap.final_url, "X-REG-SITE-TYPE": "CN", "X-Language": "zh-cn"}
    if token := cookies.get("pcftk"):
        headers["pcftk"] = token
    response = await collector.fetch(
        "https://reg.huaweicloud.com/registerui/rest/cbc/cbccustomerregservice/v1/account/check_identity",
        method="POST", headers=headers,
        json_body={"identityType": "EMAIL", "identityValue": collector.email,
                   "scenceType": "register_portal", "areaCode": "CN",
                   "clientInfo": {"devId": cookies.get("cbc-sid") or "",
                                  "hwMeta": meta.get("value", "") if meta else ""}},
    )
    if result := collector.http_result(response, (400,)):
        return result
    payload = collector.json_object(response)
    code = payload.get("error_code")
    if code:
        if not isinstance(code, str):
            raise ValueError("华为云邮箱校验的业务代码必须为字符串")
        state = "blocked" if code in {
            "CBC.7196", "CBC.7197", "CBC.7198", "CBC.7199", "CBC.7239", "CBC.7012", "CBC.7250",
        } else "unknown"
        return collector.result(state, f"邮箱校验返回 {code}，账户状态未知", response, result_code=code)
    used, available = payload["identityUsed"], payload["canUse"]
    if used not in {"true", "false"} or available not in {"true", "false"}:
        raise ValueError("华为云邮箱校验的 identityUsed 与 canUse 必须为布尔值字符串")
    if used == "true":
        return collector.result("detected", "邮箱字段检查返回已关联账户记录", response,
                                identity_used=True, registration_available=available == "true")
    if available == "true":
        return collector.result("not_detected", "邮箱字段检查返回未关联记录并允许继续注册", response,
                                identity_used=False, registration_available=True)
    return collector.result("unknown", "邮箱当前无法用于注册，接口未确认关联账户", response,
                            identity_used=False, registration_available=False)


async def jianguoyun(collector: "EmailCollector") -> "EmailObservation":
    bootstrap = await collector.fetch("https://www.jianguoyun.com/d/signup")
    if result := collector.http_result(bootstrap):
        return result
    headers = {"Referer": bootstrap.final_url, "X-Requested-With": "XMLHttpRequest"}
    session = await collector.fetch("https://www.jianguoyun.com/d/ajax/getCaptcha", headers=headers)
    if result := collector.http_result(session):
        return result
    payload = collector.json_object(session)
    if "needRealCaptcha" in payload:
        if type(payload["needRealCaptcha"]) is not bool:
            raise ValueError("坚果云的 needRealCaptcha 必须为布尔值")
        if payload["needRealCaptcha"]:
            return collector.result("blocked", "站点要求人工验证码，邮箱尚未提交", session)
    form = {key: payload[key] for key in ("custom_ticket", "exp", "sig", "reusable")}
    if not form["custom_ticket"] or not form["sig"]:
        raise ValueError("坚果云邮箱检查缺少有效的匿名会话凭据")
    form["email"] = collector.email
    response = await collector.fetch("https://www.jianguoyun.com/d/ajax/userop/verifyEmail",
                                     method="POST", headers=headers, form=form)
    if result := collector.http_result(response):
        return result
    return collector.boolean(collector.json_object(response)["isNotReg"], response, available=True)


async def zoho_cn(collector: "EmailCollector") -> "EmailObservation":
    bootstrap = await collector.fetch("https://accounts.zoho.com.cn/register")
    if result := collector.http_result(bootstrap):
        return result
    page = BeautifulSoup(collector.http.text(bootstrap), "html.parser")
    script = page.select_one('script[src*="/register/script"]')
    if script is None:
        raise ValueError("Zoho 中国站注册页面缺少字段检查配置")
    endpoint = urljoin(bootstrap.final_url, script["src"])
    location = urlsplit(endpoint)
    if (location.scheme != "https" or location.hostname != "accounts.zoho.com.cn"
            or location.path != "/register/script"):
        raise ValueError("Zoho 中国站字段检查配置地址异常")
    configuration = await collector.fetch(endpoint, headers={"Referer": bootstrap.final_url})
    if result := collector.http_result(configuration):
        return result
    program = esprima.parseScript(collector.http.text(configuration))
    tokens = [declaration.init.value for node in program.body if node.type == "VariableDeclaration"
              for declaration in node.declarations if declaration.id.name == "temp_token"
              and declaration.init.type == "Literal"]
    if len(tokens) != 1 or not isinstance(tokens[0], str) or not tokens[0]:
        raise ValueError("Zoho 中国站字段检查缺少有效的匿名会话凭据")
    response = await collector.fetch(
        "https://accounts.zoho.com.cn/webclient/v1/register/field/validate", method="POST",
        json_body={"signupvalidate": {"email": collector.email, "servicename": "AaaServer",
                                      "signupurl": bootstrap.final_url}},
        headers={"Z-Authorization": "Zoho-ticket " + tokens[0], "Referer": bootstrap.final_url,
                 "X-Requested-With": "XMLHttpRequest"},
    )
    if result := collector.http_result(response, (400,)):
        return result
    payload = collector.json_object(response)
    errors = payload.get("errors")
    if errors is not None and not isinstance(errors, list):
        raise ValueError("Zoho 中国站邮箱检查的 errors 必须为列表")
    if (payload.get("status_code") == 201 and payload.get("resource_name") == "signupvalidate"
            and not errors and not payload.get("error")):
        return collector.result("not_detected", "邮箱字段校验成功，接口允许继续注册", response)
    if errors:
        codes = [error["code"] for error in errors]
        if not all(isinstance(code, str) for code in codes):
            raise ValueError("Zoho 中国站邮箱检查的业务代码必须为字符串")
        return collector.result("unknown", "邮箱字段校验没有通过，账户状态未知", response,
                                result_codes=codes)
    raise ValueError("Zoho 中国站邮箱校验缺少明确的字段校验结果")


HANDLERS = {"huawei_cloud": huawei_cloud, "jianguoyun": jianguoyun, "zoho_cn": zoho_cn}
