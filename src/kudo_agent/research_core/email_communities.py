import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urljoin, urlsplit

import esprima
from bs4 import BeautifulSoup
from defusedxml.ElementTree import fromstring

if TYPE_CHECKING:
    from kudo_agent.research_core.email import EmailCollector
    from kudo_agent.research_core.models import Artifact


@dataclass(frozen=True)
class Community:
    name: str
    url: str
    engine: Literal["discuz", "mybb", "discourse"]
    region: str = "global"


COMMUNITIES = {
    "21ic": Community("21ic 电子技术论坛", "https://bbs.21ic.com/", "discuz", "CN"),
    "52pojie": Community("吾爱破解", "https://www.52pojie.cn/", "discuz", "CN"),
    "a9vg": Community("A9VG 电玩部落", "https://bbs.a9vg.com/", "discuz", "CN"),
    "elecfans": Community("电子发烧友论坛", "https://bbs.elecfans.com/", "discuz", "CN"),
    "enshan": Community("恩山无线论坛", "https://www.right.com.cn/forum/", "discuz", "CN"),
    "erji": Community("耳机大家坛", "https://www.erji.net/", "discuz", "CN"),
    "hostloc": Community("全球主机交流论坛", "https://hostloc.com/", "discuz", "CN"),
    "kafan": Community("卡饭论坛", "https://bbs.kafan.cn/", "discuz", "CN"),
    "keylol": Community("其乐社区", "https://keylol.com/", "discuz", "CN"),
    "mountblade": Community("骑砍中文站", "https://bbs.mountblade.com.cn/", "discuz", "CN"),
    "mydigit": Community("数码之家", "https://www.mydigit.cn/", "discuz", "CN"),
    "newwise": Community("壬天堂世界", "https://bbs.newwise.com/", "discuz", "CN"),
    "rd16": Community("一牛网论坛", "https://bbs.16rd.com/", "discuz", "CN"),
    "asus52": Community("52ASUS", "https://www.52asus.com/", "discuz", "CN"),
    "znds": Community("智能电视网", "https://www.znds.com/", "discuz", "CN"),
    "firebbs": Community("野火电子论坛", "https://www.firebbs.cn/", "discuz", "CN"),
    "hasscn": Community("瀚思彼岸智能家居", "https://bbs.hassbian.com/", "discuz", "CN"),
    "openedv": Community("正点原子论坛", "https://www.openedv.com/", "discuz", "CN"),
    "tsdm": Community("天使动漫论坛", "https://www.tsdm39.com/", "discuz", "CN"),
    "eefocus": Community("与非网论坛", "https://www.eefocus.com/forum/", "discuz", "CN"),
    "biosmods": Community("BIOS Mods", "https://www.bios-mods.com/forum/", "mybb"),
    "blitzortung": Community("Blitzortung", "https://forum.blitzortung.org/mybb/", "mybb"),
    "jellyfin": Community("Jellyfin Forum", "https://forum.jellyfin.org/", "mybb"),
    "mybb": Community("MyBB Community", "https://community.mybb.com/", "mybb"),
    "ocaml": Community("OCaml Discuss", "https://discuss.ocaml.org/", "discourse"),
    "nextpvr": Community("NextPVR", "https://forums.nextpvr.com/", "mybb"),
    "clashfarmer": Community("ClashFarmer", "https://www.clashfarmer.com/forum/", "mybb"),
    "thecardboard": Community("The CardBoard", "https://thecardboard.org/board/", "mybb"),
    "biotech": Community("Biotechnology Forums", "https://www.biotechnologyforums.com/", "mybb"),
    "appinn": Community("小众软件论坛", "https://meta.appinn.net/", "discourse", "CN"),
    "asktug": Community("TiDB 技术社区", "https://pingkai.cn/tidbcommunity/forum/", "discourse", "CN"),
    "oceanbase": Community("OceanBase 社区", "https://ask.oceanbase.com/", "discourse", "CN"),
    "haskell": Community("Haskell Community", "https://discourse.haskell.org/", "discourse"),
    "trae": Community("TRAE 中文社区", "https://forum.trae.cn/", "discourse", "CN"),
    "marginnote": Community("MarginNote 中文社区", "https://bbs.marginnote.com.cn/", "discourse", "CN"),
    "susecn": Community("SUSE 中文论坛", "https://forum.suse.org.cn/", "discourse", "CN"),
    "nvidiacn": Community("NVIDIA 开发者中文论坛", "https://forums.developer.nvidia.cn/", "discourse", "CN"),
    "jittor": Community("计图社区", "https://discuss.jittor.cn/", "discourse", "CN"),
    "openubmc": Community("openUBMC 社区", "https://discuss.openubmc.cn/", "discourse", "CN"),
    "ranchercn": Community("Rancher 中文论坛", "https://forums.rancher.cn/", "discourse", "CN"),
    "quectelcn": Community("移远中文论坛", "https://forumschinese.quectel.com/", "discourse", "CN"),
    "ruyisdk": Community("RuyiSDK 社区", "https://ruyisdk.cn/", "discourse", "CN"),
    "d2l": Community("动手学深度学习讨论区", "https://discuss.d2l.ai/", "discourse", "CN"),
    "python": Community("Python Discussions", "https://discuss.python.org/", "discourse"),
    "julia": Community("Julia Discourse", "https://discourse.julialang.org/", "discourse"),
    "ubuntu": Community("Ubuntu Discourse", "https://discourse.ubuntu.com/", "discourse"),
    "nixos": Community("NixOS Discourse", "https://discourse.nixos.org/", "discourse"),
    "homeassistant": Community("Home Assistant Community", "https://community.home-assistant.io/", "discourse"),
    "openwrt": Community("OpenWrt Forum", "https://forum.openwrt.org/", "discourse"),
    "huggingface": Community("Hugging Face Forums", "https://discuss.huggingface.co/", "discourse"),
    "pytorch": Community("PyTorch Forums", "https://discuss.pytorch.org/", "discourse"),
    "elastic": Community("Elastic Discuss", "https://discuss.elastic.co/", "discourse"),
    "gradle": Community("Gradle Forums", "https://discuss.gradle.org/", "discourse"),
    "arduino": Community("Arduino Forum", "https://forum.arduino.cc/", "discourse"),
    "freecodecamp": Community("freeCodeCamp Forum", "https://forum.freecodecamp.org/", "discourse"),
    "rubyonrails": Community("Ruby on Rails Discussions", "https://discuss.rubyonrails.org/", "discourse"),
    "django": Community("Django Forum", "https://forum.djangoproject.com/", "discourse"),
    "hashicorp": Community("HashiCorp Discuss", "https://discuss.hashicorp.com/", "discourse"),
    "kubernetes": Community("Kubernetes Discussions", "https://discuss.kubernetes.io/", "discourse"),
    "discourse": Community("Discourse Meta", "https://meta.discourse.org/", "discourse"),
    "rust": Community("Rust Users Forum", "https://users.rust-lang.org/", "discourse"),
    "swift": Community("Swift Forums", "https://forums.swift.org/", "discourse"),
    "kotlin": Community("Kotlin Discussions", "https://discuss.kotlinlang.org/", "discourse"),
    "golang": Community("Go Forum", "https://forum.golangbridge.org/", "discourse"),
    "gnome": Community("GNOME Discourse", "https://discourse.gnome.org/", "discourse"),
    "kde": Community("KDE Discuss", "https://discuss.kde.org/", "discourse"),
    "fedora": Community("Fedora Discussion", "https://discussion.fedoraproject.org/", "discourse"),
    "nextcloud": Community("Nextcloud Community", "https://help.nextcloud.com/", "discourse"),
    "syncthing": Community("Syncthing Community", "https://forum.syncthing.net/", "discourse"),
    "godot": Community("Godot Forum", "https://forum.godotengine.org/", "discourse"),
    "blender": Community("Blender Developer Talk", "https://devtalk.blender.org/", "discourse"),
    "joplin": Community("Joplin Forum", "https://discourse.joplinapp.org/", "discourse"),
    "obsidian": Community("Obsidian Forum", "https://forum.obsidian.md/", "discourse"),
}


def mybb_token(page: BeautifulSoup) -> str:
    values = {tag["value"] for tag in page.select('input[name="my_post_key"][value]')}
    for script in page.find_all("script", src=False):
        if "my_post_key" not in script.get_text():
            continue
        program = esprima.parseScript(script.get_text())
        for statement in program.body:
            if statement.type == "VariableDeclaration":
                for declaration in statement.declarations:
                    if declaration.id.name == "my_post_key" and declaration.init.type == "Literal":
                        values.add(declaration.init.value)
    if len(values) != 1:
        raise ValueError("MyBB 页面缺少唯一的邮箱字段校验令牌")
    token = values.pop()
    if not isinstance(token, str) or not token.isascii() or not token.isalnum():
        raise ValueError("MyBB 邮箱字段校验令牌格式无效")
    return token


def discourse_settings(page: BeautifulSoup) -> dict:
    tag = page.select_one("#data-preloaded")
    if tag is None:
        return {}
    data = json.loads(tag.get("data-preloaded") or tag.get_text())
    settings = data.get("siteSettings", {})
    return json.loads(settings) if isinstance(settings, str) else settings


def interpret_discuz(collector: "EmailCollector", response: "Artifact"):
    root = fromstring(collector.http.text(response))
    if root.tag != "root":
        raise ValueError("Discuz 邮箱校验返回无效的 XML 根元素")
    message = BeautifulSoup(root.text or "", "html.parser").get_text(" ", strip=True)
    attributes = {"engine": "discuz", "field_message": message}
    if message == "succeed":
        return collector.result("not_detected", "邮箱字段检查通过，未返回占用记录", response,
                                field_available=True, **attributes)
    if message in {"该 Email 地址已被注册", "該 Email 地址已被註冊"}:
        return collector.result("detected", "邮箱字段检查明确返回地址已注册", response,
                                field_available=False, **attributes)
    return collector.result("unknown", "邮箱字段检查未确认注册状态", response, **attributes)


def interpret_mybb(collector: "EmailCollector", response: "Artifact"):
    payload = json.loads(collector.http.text(response))
    if payload == "true":
        return collector.result("unknown", "邮箱字段允许继续填写；站点可能允许多个账户使用同一邮箱",
                                response, engine="mybb", field_available=True, registration_status="unknown")
    if not isinstance(payload, (str, dict)):
        raise ValueError("MyBB 邮箱字段检查返回无效的 JSON 类型")
    if isinstance(payload, str) and "email address that is already in use by another member" in payload:
        return collector.result("detected", "邮箱字段明确返回已被其他账户使用", response,
                                engine="mybb", field_available=False)
    return collector.result("unknown", "邮箱字段检查未确认注册状态", response,
                            engine="mybb", field_result=payload)


def interpret_discourse(collector: "EmailCollector", response: "Artifact", settings: dict):
    payload = collector.json_object(response)
    hidden = settings.get("hide_email_address_taken")
    if hidden is not None and type(hidden) is not bool:
        raise ValueError("Discourse 隐私设置必须为布尔值")
    attributes = {"engine": "discourse", "hide_email_address_taken": hidden}
    errors = payload.get("errors", [])
    if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
        raise ValueError("Discourse 邮箱字段检查返回无效的错误列表")
    if any(error.rstrip(".").lower() == "email has already been taken" for error in errors):
        return collector.result("detected", "邮箱字段明确返回已被占用", response, **attributes)
    if payload.get("success") == "OK":
        if hidden is False:
            return collector.result("not_detected", "站点公开邮箱占用状态，字段检查未返回占用记录",
                                    response, field_available=True, **attributes)
        return collector.result("unknown", "接口接受了字段检查，站点未公开可核实的邮箱占用状态",
                                response, registration_status="unknown", **attributes)
    if errors or "failed" in payload or "error_type" in payload:
        return collector.result("unknown", "邮箱字段检查未确认注册状态", response,
                                field_errors=errors, **attributes)
    raise ValueError("Discourse 邮箱字段检查返回未定义的响应结构")


async def community(collector: "EmailCollector"):
    site = COMMUNITIES[collector.site]
    bootstrap = await collector.fetch(site.url)
    if result := collector.http_result(bootstrap):
        return result
    page = BeautifulSoup(collector.http.text(bootstrap), "html.parser")
    if urlsplit(bootstrap.final_url).hostname != urlsplit(site.url).hostname:
        return collector.result("unsupported", "社区入口已迁移，邮箱校验地址需要重新核实", bootstrap)
    engine_visible = site.engine in str(page).lower()
    if not engine_visible:
        return collector.result("unsupported", "当前页面未提供已适配的社区协议", bootstrap)
    if site.engine == "discuz":
        response = await collector.fetch(urljoin(site.url, "forum.php"), follow_redirects=False, params={
            "mod": "ajax", "inajax": "yes", "infloat": "register", "handlekey": "register",
            "ajaxmenu": "1", "action": "checkemail", "email": collector.email,
        })
    elif site.engine == "mybb":
        if "Human Verification" in page.get_text() and page.select_one(".h-captcha, .g-recaptcha"):
            return collector.result("blocked", "社区页面要求人工验证，邮箱尚未提交", bootstrap)
        response = await collector.fetch(urljoin(site.url, "xmlhttp.php"), follow_redirects=False,
                                        params={"action": "email_availability", "email": collector.email,
                                                "my_post_key": mybb_token(page)},
                                        headers={"X-Requested-With": "XMLHttpRequest", "Referer": site.url})
    else:
        response = await collector.fetch(urljoin(site.url, "u/check_email.json"),
                                        params={"email": collector.email}, follow_redirects=False,
                                        headers={"Accept": "application/json"})
    if response.status_code in {301, 302, 303, 307, 308}:
        location = response.headers.get("location")
        if not location:
            raise ValueError("邮箱字段校验的重定向响应缺少 Location")
        destination = urlsplit(urljoin(response.final_url, location))
        if destination.scheme not in {"http", "https"} or not destination.hostname:
            raise ValueError("邮箱字段校验返回无效的重定向地址")
        if "captcha" in destination.hostname or "captcha" in destination.path:
            return collector.result("blocked", "字段校验要求访问验证码页面", response)
        return collector.result("unknown", "字段校验要求跳转，当前响应未确认账户状态", response)
    if result := collector.http_result(response, (422,)):
        return result
    if site.engine == "discuz":
        if "xml" not in response.headers.get("content-type", ""):
            return collector.result("unsupported", "当前地址未返回 Discuz 邮箱校验协议", response)
        return interpret_discuz(collector, response)
    if "json" not in response.headers.get("content-type", ""):
        return collector.result("unsupported", "当前地址未返回社区邮箱校验协议", response)
    if site.engine == "mybb":
        return interpret_mybb(collector, response)
    return interpret_discourse(collector, response, discourse_settings(page))
