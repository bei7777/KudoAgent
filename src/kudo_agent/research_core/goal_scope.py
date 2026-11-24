import re
from urllib.parse import urlsplit

from kudo_agent.research_core.catalog import Catalog, Site
from kudo_agent.research_core.email_sites import EMAIL_PROVIDERS
from kudo_agent.research_core.models import EMAIL_SITES, EmailSite, ResearchRequest

# 明确限定平台的子句可以包含并列名称。逗号后的独立指令仍属于研究问题。
PLATFORM_CLAUSE = re.compile(
    r"(?:^|[，,。；;！!？?\n])\s*(?:请\s*)?"
    r"(?:(?P<exclusive>仅限于?|仅|只)\s*(?:想|需要|需)?\s*(?:在|查找|查看|查询|检查|核查|核实|验证|查|看)?"
    r"|(?:查找|查看|查询|检查|核查|核实|验证|比较|对比))\s*"
    r"(?:(?:这个|该|此)?(?:项目|账号|用户)(?:在|的)\s*)?"
    r"(?P<platforms>[^。；;！!？?\n]+?)(?=[。；;！!？?\n]|[,，]\s*(?:请|仅|只|附上|给出|列出|说明|比较|对比|整理|并|不要)|$)"
)
PLATFORM_SUFFIX = re.compile(r"\s*(?:(?:这|上述)(?:两|三|几|些|\d+)个?)?(?:上|中)?的?(?:公开)?(?:用户)?"
                             r"(?:主页|账号|账户|页面|站点|平台|网站|查找|查询|核查)$")
LIST_SEPARATOR = re.compile(r"\s*(?:[、，,]\s*(?:以及|和|与|及|\band\b)?|以及|和|与|及|或|\band\b|[&＆/])\s*",
                            re.IGNORECASE)


def site_names(site: Site) -> set[str]:
    return {value.casefold() for value in (site.name, *site.aliases, *(rule.name for rule in site.rules))}


def bind_goal_site(catalog: Catalog, request: ResearchRequest) -> ResearchRequest:
    clauses = list(PLATFORM_CLAUSE.finditer(request.goal))
    kinds = {item.kind for item in request.identifiers}
    selected = {}
    if (clauses or request.goal_sites) and (not kinds or kinds & {"username", "email", "profile"}):
        known = {name for site in catalog.sites.values() for name in site_names(site)}
        known.update(name.casefold() for site, provider in EMAIL_PROVIDERS.items() for name in (site, provider.name))
        for clause in clauses:
            phrase = clause["platforms"].strip().removesuffix(".").strip()
            candidate = phrase if phrase.casefold() in known else PLATFORM_SUFFIX.sub("", phrase).strip()
            names = [candidate] if candidate.casefold() in known else LIST_SEPARATOR.split(candidate)
            recognized = any(name.casefold() in known for name in names)
            names_platforms = clause["exclusive"] and (
                phrase.endswith(("平台", "站点", "网站"))
                or any(re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]+", name) or name.endswith("网") for name in names)
            )
            if not recognized and not names_platforms:
                continue
            for name in names:
                key = name.casefold()
                if key in known:
                    selected.setdefault(key, name)
                else:
                    raise ValueError(f"目录中找不到研究问题指定的站点“{name}”，请核对平台名称。")
        if request.goal_sites:
            supplied = {name.casefold(): name for name in request.goal_sites}
            for key in supplied.keys() - known:
                raise ValueError(f"目录中找不到研究问题指定的站点“{supplied[key]}”，请核对平台名称。")
            if selected and selected.keys() != supplied.keys():
                raise ValueError("提交的平台名单与研究问题指定的平台不一致，请核对查询范围。")
            selected = selected or supplied
    names = list(selected.values())
    return request.model_copy(update={"goal_sites": names, "goal_site": names[0] if len(names) == 1 else None})


def limit_to_goal_sites(request: ResearchRequest, usernames: list[Site],
                        emails: list[EmailSite]) -> tuple[list[Site], list[EmailSite]]:
    order = {name.casefold(): index for index, name in enumerate(request.goal_sites)}
    email_order = order.copy()
    matched = []
    for site in usernames:
        names = order.keys() & site_names(site)
        if site.supported and names:
            position = min(order[name] for name in names)
            matched.append((position, site))
            email_order.setdefault(site.name.casefold(), position)
    if not request.sites:
        matched.sort(key=lambda pair: pair[0])
    selected = [site for _, site in matched]
    providers = [key for key in emails if key in email_order or EMAIL_PROVIDERS[key].name.casefold() in email_order]
    if tuple(request.email_sites) == EMAIL_SITES:
        providers.sort(key=lambda key: min(email_order[name] for name in (key, EMAIL_PROVIDERS[key].name.casefold())
                                           if name in email_order))
    kinds = {item.kind for item in request.identifiers}
    platforms = "、".join(request.goal_sites)
    if ("username" in kinds and not selected) or ("email" in kinds and not providers):
        raise ValueError(f"研究问题指定了 {platforms}，当前所选范围没有适用站点。")
    return selected, providers


def validate_goal_profiles(catalog: Catalog, request: ResearchRequest):
    names = {name.casefold() for name in request.goal_sites}
    # 使用目录中的主页域名；直接读取给定地址无需该平台支持用户名查询。
    domains = {(urlsplit(site.template).hostname or "").removeprefix("www.") for site in catalog.sites.values()
               if names & site_names(site)}
    for item in request.identifiers:
        if item.kind == "profile" and (urlsplit(item.value).hostname or "").removeprefix("www.") not in domains:
            raise ValueError(f"所填主页地址与研究问题指定的 {'、'.join(request.goal_sites)} 不一致，请核对地址或研究问题。")
