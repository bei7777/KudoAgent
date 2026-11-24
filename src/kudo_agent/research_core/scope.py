from dataclasses import dataclass

from kudo_agent.research_core.catalog import Catalog, Site
from kudo_agent.research_core.email_sites import EMAIL_PROVIDERS
from kudo_agent.research_core.goal_scope import limit_to_goal_sites, validate_goal_profiles
from kudo_agent.research_core.models import EMAIL_SITES, EmailSite, ResearchRequest


@dataclass
class ResolvedSites:
    usernames: list[Site]
    emails: list[EmailSite]

    def counts(self) -> dict[str, int]:
        return {"username_sites": len(self.usernames), "email_sites": len(self.emails)}


def resolve_sites(catalog: Catalog, request: ResearchRequest) -> ResolvedSites:
    categories = request.site_categories
    maximum = request.budget.max_sites
    kinds = {identifier.kind for identifier in request.identifiers}
    direct_pages = kinds == {"profile"} and request.budget.max_depth == 0
    if request.goal_sites and "profile" in kinds:
        validate_goal_profiles(catalog, request)
    if direct_pages:
        return ResolvedSites([], [])
    if request.follow_profile_links is True and not (request.sites or categories or request.goal_sites):
        return ResolvedSites([], [])
    if categories is None and not request.goal_sites:
        return ResolvedSites(catalog.select(request.sites, maximum), request.email_sites or list(EMAIL_SITES))
    if request.sites:
        sites = [site for site in catalog.select(request.sites) if site.supported and
                 (categories is None or site.category in categories)]
    else:
        sites = catalog.select_categories(categories) if categories else catalog.select([])
    allowed_email = request.email_sites if categories else request.email_sites or list(EMAIL_SITES)
    emails = [site for site in allowed_email if categories is None or EMAIL_PROVIDERS[site].category in categories]
    if request.goal_sites:
        sites, emails = limit_to_goal_sites(request, sites, emails)
    if (request.sites or request.goal_sites) and maximum is not None and len(sites) > maximum:
        raise ValueError("指定站点数量超过 max_sites")
    sites = sites[:maximum]
    if (kinds and kinds <= {"username", "email"}
            and not (("username" in kinds and sites) or ("email" in kinds and emails))):
        raise ValueError("所选站点范围没有适用于当前线索的查询，请修改分类或指定站点")
    return ResolvedSites(sites, emails)
