from collections.abc import Iterable
from typing import Literal

SiteCategory = Literal["social", "technology", "media", "leisure", "knowledge", "commerce", "other"]

SITE_CATEGORIES: dict[SiteCategory, str] = {
    "social": "社交与综合社区",
    "technology": "技术与开发",
    "media": "媒体与创作",
    "leisure": "游戏与兴趣",
    "knowledge": "知识与教育",
    "commerce": "商业与职业",
    "other": "其他站点",
}

# 某些国内规则只有地区标签；同一平台的不同主页模板也可能只有一条带有主题标签。
PLATFORM_DOMAINS: dict[SiteCategory, tuple[str, ...]] = {
    "social": ("twitter.com", "x.com", "reddit.com", "weibo.com", "gravatar.com", "en.gravatar.com", "plurk.com"),
    "technology": ("gitee.com", "github.com", "gitlab.com", "cnblogs.com", "blog.csdn.net", "juejin.cn",
                   "v2ex.com", "gitcode.com", "sourceforge.net", "replit.com"),
    "media": ("youtube.com", "flickr.com", "pinterest.com", "soundcloud.com", "open.spotify.com",
              "space.bilibili.com", "{username}.wordpress.com", "imgur.com"),
    "leisure": ("douban.com", "twitch.tv", "twitchtracker.com", "steamcommunity.com"),
    "knowledge": ("zhihu.com", "en.wikipedia.org", "archive.org", "duolingo.com"),
    "commerce": ("linkedin.com", "ebay.com", "freelancer.com"),
}
DOMAIN_CATEGORY = {domain: category for category, domains in PLATFORM_DOMAINS.items() for domain in domains}

# 专题标签优先于 forum、discussion 等网站形式标签；每个目录条目只计入一个类别。
TOPIC_TAGS: tuple[tuple[SiteCategory, frozenset[str]], ...] = (
    ("technology", frozenset(("coding", "hacking", "llm"))),
    ("leisure", frozenset(("gaming", "anime", "sport", "cooking", "travel", "auto", "fashion", "gambling"))),
    ("media", frozenset(("video", "music", "streaming", "photo", "art", "design", "3d", "movies", "writing", "blog"))),
    ("knowledge", frozenset(("education", "research", "science", "books", "reading", "medicine", "q&a", "psychology"))),
    ("commerce", frozenset(("shopping", "professional", "finance", "crypto", "business", "freelance",
                            "trading", "career", "classified", "fintech", "stock", "nft"))),
    ("technology", frozenset(("tech", "apps"))),
    ("knowledge", frozenset(("wiki", "archive", "documents"))),
    ("leisure", frozenset(("hobby", "parenting"))),
    ("social", frozenset(("social", "mastodon", "lemmy", "geosocial", "messaging", "forum", "discussion",
                          "networking", "dating"))),
    ("media", frozenset(("news", "sharing"))),
)


def username_category(host: str, tags: set[str]) -> SiteCategory:
    if host in DOMAIN_CATEGORY:
        return DOMAIN_CATEGORY[host]
    return next((category for category, topics in TOPIC_TAGS if topics & tags), "other")


def checked_categories(categories: Iterable[str]) -> set[str]:
    selected = set(categories)
    unknown = selected - SITE_CATEGORIES.keys()
    if unknown:
        raise ValueError(f"无法识别的站点类别：{', '.join(sorted(unknown))}")
    return selected
