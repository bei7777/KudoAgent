import json
from dataclasses import asdict, dataclass
from importlib.resources import files

from pydantic import StrictBool, model_validator

from kudo_agent.research_core.models import EMAIL_SITES, EmailSite, Identifier, Model


@dataclass(frozen=True)
class EmailProvider:
    name: str
    interaction: str
    source: str
    meaning: str
    region: str = "global"


EMAIL_PROVIDERS = {
    "gitee": EmailProvider("Gitee", "account_status", "https://gitee.com/login",
                           "邮箱登录流程中的独立账户状态检查", region="CN"),
    "tencent_cloud": EmailProvider("腾讯云", "registration_check", "https://cloud.tencent.com/register",
                                   "注册表单的邮箱账户检查，保留腾讯云及 DNSPod 账户体系说明", region="CN"),
    "cnblogs": EmailProvider("博客园", "registration_check", "https://account.cnblogs.com/signup",
                             "注册表单的独立邮箱字段校验", region="CN"),
    "jd": EmailProvider("京东", "registration_check", "https://reg.jd.com/reg/person",
                        "注册表单的独立邮箱字段校验", region="CN"),
    "github": EmailProvider("GitHub", "registration_check", "https://github.com/signup",
                            "注册表单的邮箱字段校验"),
    "pinterest": EmailProvider("Pinterest", "registration_check", "https://www.pinterest.com/signup/",
                               "注册页面的邮箱存在性查询"),
    "spotify": EmailProvider("Spotify", "registration_check", "https://www.spotify.com/signup",
                             "注册接口的邮箱字段校验"),
    "mozilla": EmailProvider("Mozilla", "account_status", "https://mozilla.github.io/ecosystem-platform/api",
                             "Mozilla 账户状态接口返回的存在性"),
    "wordpress": EmailProvider("WordPress.com", "authentication_options", "https://wordpress.com/log-in",
                               "WordPress.com 登录方式查询中的账户记录"),
    "gravatar": EmailProvider("Gravatar", "public_avatar", "https://docs.gravatar.com/sdk/images/",
                              "仅检查邮箱摘要关联的公开头像，未发现头像时账户注册状态仍未知"),
    "duolingo": EmailProvider("Duolingo", "public_profile", "https://www.duolingo.com/",
                              "按邮箱查询返回的可访问用户记录"),
    "replit": EmailProvider("Replit", "account_status", "https://replit.com/signup",
                            "账户存在性接口的明确结果"),
    "imgur": EmailProvider("Imgur", "registration_check", "https://imgur.com/register",
                           "注册页面的邮箱可用性校验"),
    "lastfm": EmailProvider("Last.fm", "registration_check", "https://www.last.fm/join",
                            "注册表单的独立邮箱字段校验"),
    "freelancer": EmailProvider("Freelancer", "registration_check", "https://www.freelancer.com/signup",
                                "用户字段检查接口返回的邮箱可用性"),
}


def email_capabilities() -> list[dict]:
    checks = json.loads(files("kudo_agent.research_core").joinpath("data/email-verification.json").read_text(encoding="utf-8"))
    if checks["providers"].keys() != EMAIL_PROVIDERS.keys():
        raise ValueError("邮箱提供方与验证记录不一致")
    return [{"site": site, **asdict(EMAIL_PROVIDERS[site]), "last_check": {
        "checked_on": checks["checked_on"], "accuracy": checks["accuracy"], **checks["providers"][site],
    }} for site in EMAIL_SITES]


class EmailSample(Model):
    site: EmailSite
    email: str
    owned: StrictBool
    registered: StrictBool | None = None
    observed: StrictBool | None = None

    @model_validator(mode="after")
    def validate_sample(self):
        if not self.owned:
            raise ValueError("样本必须明确声明为自有邮箱")
        self.email = Identifier(kind="email", value=self.email).value
        return self

    @property
    def expected_presence(self) -> bool | None:
        if EMAIL_PROVIDERS[self.site].interaction in {"public_avatar", "public_profile"}:
            return self.observed
        return self.registered
