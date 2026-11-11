import hashlib
import json
import time
from dataclasses import dataclass, field
from functools import partial
from urllib.parse import quote, urljoin, urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup

from kudo_agent.research_core.email_communities import COMMUNITIES, community
from kudo_agent.research_core.email_developer import HANDLERS as DEVELOPER_HANDLERS
from kudo_agent.research_core.email_domestic import HANDLERS as DOMESTIC_HANDLERS
from kudo_agent.research_core.email_products import HANDLERS as PRODUCT_HANDLERS
from kudo_agent.research_core.email_sites import EMAIL_PROVIDERS
from kudo_agent.research_core.email_social import HANDLERS as SOCIAL_HANDLERS
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import Artifact, RequestSpec
from kudo_agent.upstream.maigret import detect_error_page


@dataclass
class EmailObservation:
    state: str
    summary: str
    source: str
    artifacts: list[Artifact]
    attributes: dict = field(default_factory=dict)


class EmailCollector:
    def __init__(self, http: HttpExecutor, site: str, email: str, action_id: str):
        self.http, self.site, self.email = http, site, email
        self.scope = f"email:{action_id}:{uuid4()}"
        self.artifacts: list[Artifact] = []

    async def collect(self) -> EmailObservation:
        handlers = {"github": self.github, "pinterest": self.pinterest, "spotify": self.spotify,
                    "mozilla": self.mozilla, "wordpress": self.wordpress, "gravatar": self.gravatar,
                    "duolingo": self.duolingo, "replit": self.replit, "imgur": self.imgur,
                    "lastfm": self.lastfm, "freelancer": self.freelancer,
                    "cnblogs": self.cnblogs, "jd": self.jd, "gitee": self.gitee,
                    "tencent_cloud": self.tencent_cloud}
        handlers.update({name: partial(handler, self) for name, handler in SOCIAL_HANDLERS.items()})
        handlers.update({name: partial(handler, self) for name, handler in DOMESTIC_HANDLERS.items()})
        handlers.update({name: partial(handler, self) for name, handler in PRODUCT_HANDLERS.items()})
        handlers.update({name: partial(handler, self) for name, handler in DEVELOPER_HANDLERS.items()})
        handlers.update({name: partial(community, self) for name in COMMUNITIES})
        if self.site not in handlers:
            raise ValueError(f"未知邮箱提供方：{self.site}")
        return await handlers[self.site]()

    async def fetch(self, url: str, **parameters) -> Artifact:
        artifact = await self.http.fetch(RequestSpec(
            url=url, identity_scope=self.scope, account_request=True, **parameters,
        ))
        self.artifacts.append(artifact)
        return artifact

    def result(self, state: str, reason: str, artifact: Artifact, **attributes) -> EmailObservation:
        provider = EMAIL_PROVIDERS[self.site]
        return EmailObservation(
            state, f"{provider.name}：{reason}", artifact.url, list(self.artifacts),
            {"provider": self.site, "interaction": provider.interaction, "meaning": provider.meaning, **attributes},
        )

    def http_result(self, artifact: Artifact, expected: tuple[int, ...] = ()) -> EmailObservation | None:
        text = "" if artifact.headers.get("content-type", "").startswith("image/") else self.http.text(artifact)
        problem = detect_error_page(text, artifact.status_code, expected_statuses=expected)
        if problem:
            return self.result(problem, f"HTTP {artifact.status_code} 或服务限制，无法判定", artifact)
        if artifact.status_code in {404, 410} and artifact.status_code not in expected:
            return self.result("unsupported", "当前查询接口不可用", artifact)
        return None

    def json_object(self, artifact: Artifact) -> dict:
        value = json.loads(self.http.text(artifact))
        if not isinstance(value, dict):
            raise ValueError(f"{self.site} 接口必须返回 JSON 对象")
        return value

    def boolean(self, value, artifact: Artifact, *, available: bool = False) -> EmailObservation:
        if type(value) is not bool:
            raise ValueError(f"{self.site} 接口的存在性字段必须为布尔值")
        exists = not value if available else value
        return self.result("detected" if exists else "not_detected",
                           "接口返回存在记录" if exists else "接口返回未发现记录", artifact)

    async def github(self):
        bootstrap = await self.fetch("https://github.com/signup")
        if result := self.http_result(bootstrap):
            return result
        soup = BeautifulSoup(self.http.text(bootstrap), "html.parser")
        check = soup.select_one('auto-check[src*="email"]')
        if check is None:
            raise ValueError("GitHub 注册页面缺少邮箱校验声明")
        token = check.select_one('input[name="authenticity_token"]')
        if token is None:
            raise ValueError("GitHub 邮箱校验缺少 CSRF 令牌")
        endpoint = urljoin(bootstrap.final_url, check["src"])
        if urlsplit(endpoint).hostname != "github.com":
            raise ValueError("GitHub 邮箱校验地址异常")
        response = await self.fetch(endpoint, method="POST", follow_redirects=False,
                                    form={"value": self.email, "authenticity_token": token["value"]})
        if result := self.http_result(response, (422,)):
            return result
        message = BeautifulSoup(self.http.text(response), "html.parser").get_text(" ", strip=True).lower()
        if response.status_code == 422 and "email" in message and ("already" in message or "taken" in message):
            return self.boolean(True, response)
        if response.status_code == 200:
            return self.boolean(False, response)
        return self.result("unknown", "邮箱校验没有返回明确结果", response)

    async def gitee(self):
        bootstrap = await self.fetch("https://gitee.com/login")
        if result := self.http_result(bootstrap):
            return result
        page = BeautifulSoup(self.http.text(bootstrap), "html.parser")
        csrf = page.select_one('meta[name="csrf-token"]')
        if csrf is None or not csrf.get("content"):
            raise ValueError("Gitee 登录页面缺少 CSRF 令牌")
        response = await self.fetch("https://gitee.com/check_oversea_email", method="POST",
                                    form={"number": self.email},
                                    headers={"X-CSRF-Token": csrf["content"],
                                             "X-Requested-With": "XMLHttpRequest",
                                             "Referer": bootstrap.final_url})
        if result := self.http_result(response):
            return result
        payload = self.json_object(response)
        code = payload["status"]
        if type(code) not in {int, str}:
            raise ValueError("Gitee 邮箱检查缺少明确的业务状态")
        if str(code) in {"0", "1"}:
            return self.boolean(str(code) == "1", response)
        if str(code) in {"2", "3"}:
            return self.result("unknown", "邮箱状态检查需要补充验证，账户状态未知", response,
                               result_code=code)
        raise ValueError(f"Gitee 返回未定义的邮箱检查状态：{code}")

    async def tencent_cloud(self):
        response = await self.fetch("https://cloud.tencent.com/auth-api/register/checkAccountExist",
                                    params={"t": str(time.time_ns() // 1_000_000),
                                            "payload": json.dumps({"username": self.email})},
                                    headers={"Referer": "https://cloud.tencent.com/register"})
        if result := self.http_result(response):
            return result
        payload = self.json_object(response)
        if type(payload.get("success")) not in {bool, int} or payload["success"] not in {0, 1}:
            raise ValueError("腾讯云邮箱校验缺少有效的 success 状态")
        if payload["success"]:
            return self.result("not_detected", "邮箱字段校验通过，接口允许继续注册", response)
        code = payload["code"]
        if not isinstance(code, str):
            raise ValueError("腾讯云邮箱校验业务代码必须为字符串")
        if code in {"Account.AlreadyExist", "Account.AlreadyExistInDNSPOD", "Account.AreaNotMatch"}:
            return self.result("detected", "邮箱对应腾讯云或关联账户体系的记录", response,
                               result_code=code, account_scope="腾讯云及 DNSPod 账户体系")
        return self.result("unknown", f"邮箱校验返回 {code}，账户状态未知", response, result_code=code)

    async def pinterest(self):
        bootstrap = await self.fetch("https://www.pinterest.com/signup/")
        if result := self.http_result(bootstrap):
            return result
        page = BeautifulSoup(self.http.text(bootstrap), "html.parser")
        configuration = page.find("script", id="__PWS_DATA__", type="application/json")
        if configuration is None:
            raise ValueError("Pinterest 注册页面缺少客户端配置")
        app_version = json.loads(configuration.get_text())["appVersion"]
        response = await self.fetch(
            "https://www.pinterest.com/resource/EmailExistsResource/get/",
            params={"source_url": "/signup/", "data": json.dumps({"options": {"email": self.email}, "context": {}})},
            headers={"X-Requested-With": "XMLHttpRequest", "X-App-Version": str(app_version),
                     "Accept": "application/json, text/javascript, */*, q=0.01", "Referer": bootstrap.final_url},
        )
        if result := self.http_result(response):
            return result
        data = self.json_object(response)["resource_response"]["data"]
        if isinstance(data, dict) and "source_field" in data:
            return self.result("unknown", "服务拒绝查询或返回限制信息", response)
        return self.boolean(data, response)

    async def spotify(self):
        response = await self.fetch("https://spclient.wg.spotify.com/signup/public/v1/account",
                                    params={"validate": "1", "email": self.email})
        if result := self.http_result(response):
            return result
        status = self.json_object(response)["status"]
        if type(status) is not int:
            raise ValueError("Spotify 业务状态必须为整数")
        if status in {1, 20}:
            return self.boolean(status == 20, response)
        return self.result("unknown", f"无法判定的业务状态 {status}", response)

    async def mozilla(self):
        response = await self.fetch("https://api.accounts.firefox.com/v1/account/status",
                                    method="POST", json_body={"email": self.email})
        if result := self.http_result(response, (400,)):
            return result
        payload = self.json_object(response)
        if payload.get("errno") == 125:
            return self.result("blocked", "Mozilla 拒绝了账户状态查询", response)
        if response.status_code != 200:
            raise ValueError(f"Mozilla 账户状态请求被拒绝：{payload.get('errno')}")
        return self.boolean(payload["exists"], response)

    async def wordpress(self):
        response = await self.fetch(
            f"https://public-api.wordpress.com/rest/v1.1/users/{quote(self.email, safe='')}/auth-options",
            params={"http_envelope": "1", "locale": "en"},
        )
        if result := self.http_result(response):
            return result
        envelope = self.json_object(response)
        body = envelope["body"]
        if not isinstance(body, dict):
            raise ValueError("WordPress.com 登录方式响应缺少对象 body")
        status = envelope["code"]
        if status == 429:
            return self.result("rate_limited", "账户查询达到限流条件", response)
        if status in {401, 403}:
            return self.result("blocked", "账户查询受到限制", response)
        if body.get("error") == "unknown_user":
            return self.boolean(False, response)
        if status == 200 and type(body.get("email_verified")) is bool:
            return self.result("detected", "服务返回账户记录", response, email_verified=body["email_verified"])
        if "error" in body:
            return self.result("unknown", f"服务返回 {body['error']}，无法判定", response)
        raise ValueError("WordPress.com 返回了未知的登录方式结构")

    async def gravatar(self):
        key = hashlib.sha256(self.email.strip().lower().encode()).hexdigest()
        response = await self.fetch(f"https://gravatar.com/avatar/{key}", params={"d": "404", "s": "80"})
        if result := self.http_result(response, (404,)):
            return result
        if response.status_code == 404:
            return self.result("not_detected", "未发现指定显示等级的公开头像，账户注册状态未知", response,
                               registration_status="unknown", avatar_rating="g")
        if response.status_code == 200 and response.headers.get("content-type", "").startswith("image/"):
            return self.result("detected", "发现邮箱摘要关联的公开头像", response,
                               registration_status="unknown", avatar_rating="g")
        raise ValueError("Gravatar 没有返回头像或明确的缺失状态")

    async def duolingo(self):
        response = await self.fetch("https://www.duolingo.com/2017-06-30/users", params={"email": self.email})
        if result := self.http_result(response):
            return result
        users = self.json_object(response)["users"]
        if not isinstance(users, list):
            raise ValueError("Duolingo users 字段必须为列表")
        return self.result("detected" if users else "not_detected",
                           "发现可访问的用户记录" if users else "当前查询未返回用户记录", response,
                           returned_records=len(users), scope="accessible_user_records", registration_status="unknown")

    async def replit(self):
        response = await self.fetch("https://replit.com/data/user/exists", method="POST", json_body={"email": self.email})
        if result := self.http_result(response):
            return result
        return self.boolean(self.json_object(response)["exists"], response)

    async def imgur(self):
        bootstrap = await self.fetch("https://imgur.com/register")
        if result := self.http_result(bootstrap):
            return result
        response = await self.fetch("https://imgur.com/signin/ajax_email_available", method="POST", form={"email": self.email},
                                    headers={"X-Requested-With": "XMLHttpRequest", "Origin": "https://imgur.com"})
        if result := self.http_result(response, (412,)):
            return result
        payload = self.json_object(response)
        if response.status_code == 412 and payload.get("success") is False:
            return self.result("blocked", "邮箱查询未通过站点安全检查", response)
        if payload.get("success") is False:
            return self.result("unknown", "邮箱可用性检查返回业务错误", response)
        data = payload["data"]
        if data.get("error") or data.get("message"):
            return self.result("unknown", "邮箱可用性检查返回额外限制信息", response)
        return self.boolean(data["available"], response, available=True)

    async def lastfm(self):
        bootstrap = await self.fetch("https://www.last.fm/join")
        if result := self.http_result(bootstrap):
            return result
        soup = BeautifulSoup(self.http.text(bootstrap), "html.parser")
        token = soup.select_one('input[name="csrfmiddlewaretoken"]')
        if token is None:
            raise ValueError("Last.fm 注册页面缺少 CSRF 字段")
        response = await self.fetch("https://www.last.fm/join/partial/validate", method="POST",
                                    form={"csrfmiddlewaretoken": token["value"], "email": self.email},
                                    headers={"Referer": bootstrap.final_url, "X-Requested-With": "XMLHttpRequest"})
        if result := self.http_result(response):
            return result
        data = self.json_object(response)["email"]
        if data["valid"] is True:
            return self.boolean(False, response)
        messages = data.get("error_messages", [])
        if any("already registered" in message.lower() for message in messages):
            return self.boolean(True, response)
        return self.result("unknown", "邮箱字段校验未提供明确的注册状态", response)

    async def freelancer(self):
        response = await self.fetch("https://www.freelancer.com/api/users/0.1/users/check", method="POST",
                                    params={"compact": "true", "new_errors": "true"}, json_body={"user": {"email": self.email}})
        if result := self.http_result(response, (409,)):
            return result
        payload = self.json_object(response)
        if response.status_code == 409 and payload.get("error_code") == "EMAIL_ALREADY_IN_USE":
            return self.boolean(True, response)
        if response.status_code == 200 and payload.get("status") == "success":
            return self.boolean(False, response)
        return self.result("unknown", "用户字段检查未返回明确的邮箱状态", response)

    async def cnblogs(self):
        bootstrap = await self.fetch("https://account.cnblogs.com/signup")
        if result := self.http_result(bootstrap):
            return result
        response = await self.fetch("https://account.cnblogs.com/account/checkEmail", method="POST",
                                    multipart={"email": self.email}, follow_redirects=False,
                                    headers={"Referer": bootstrap.final_url})
        if result := self.http_result(response):
            return result
        value = json.loads(self.http.text(response))
        if value is True:
            return self.result("not_detected", "邮箱字段校验通过，接口表示可用于注册", response)
        if isinstance(value, str):
            if "已被注册" in value or "已经被注册" in value or "已存在" in value:
                return self.result("detected", "邮箱字段校验返回已使用提示", response)
            return self.result("unknown", "邮箱字段校验未明确说明账户状态", response, validation_message=value)
        raise ValueError("博客园邮箱字段校验必须返回 true 或错误说明")

    async def jd(self):
        bootstrap = await self.fetch("https://reg.jd.com/reg/person")
        if result := self.http_result(bootstrap):
            return result
        soup = BeautifulSoup(self.http.text(bootstrap), "html.parser")
        fields = {key: soup.select_one(f"input#{key}") for key in ("uuid", "source", "token", "eid")}
        if any(value is None for value in fields.values()):
            raise ValueError("京东注册页面缺少邮箱校验参数")
        form = {key: element["value"] for key, element in fields.items() if key != "token"}
        form[fields["token"]["name"]] = fields["token"]["value"]
        form["email"] = self.email
        response = await self.fetch("https://reg.jd.com/p/checkEmail", method="POST", form=form,
                                    follow_redirects=False, headers={"Referer": bootstrap.final_url})
        if result := self.http_result(response):
            return result
        code = self.json_object(response)["resultCode"]
        if type(code) is not int:
            raise ValueError("京东邮箱字段校验的 resultCode 必须为整数")
        if code in {1, 708}:
            return self.boolean(code == 708, response)
        if code == 608:
            return self.result("blocked", "邮箱字段校验要求有效验证码", response, result_code=code)
        return self.result("unknown", f"邮箱字段校验返回业务状态 {code}，无法判定", response, result_code=code)
