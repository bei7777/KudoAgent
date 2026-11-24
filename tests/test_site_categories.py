from collections import Counter
from typing import get_args

import pytest

from kudo_agent.application.config import Settings
from kudo_agent.application.service import Application
from kudo_agent.research_core.email_sites import EMAIL_PROVIDERS
from kudo_agent.research_core.site_categories import SITE_CATEGORIES, SiteCategory


def test_category_partition_matches_real_catalog(catalog):
    assert set(get_args(SiteCategory)) == SITE_CATEGORIES.keys()
    selected = catalog.select([])
    combined = [s.site_id for category in SITE_CATEGORIES for s in catalog.select_categories([category])]
    assert len(combined) == len(set(combined)) == len(selected)
    assert set(combined) == {s.site_id for s in selected}
    categories = ["knowledge", "technology"]
    expected = [s for s in selected if s.category in categories]
    assert catalog.select_categories([*categories, "technology"], 20) == expected[:20]
    for name, category in (("GitHub", "technology"), ("Gitee", "technology"), ("Zhihu", "knowledge"),
                           ("Steam", "leisure"), ("LinkedIn", "commerce"), ("Reddit", "social")):
        assert catalog.select([name])[0].public()["category"] == category
    assert {s.category for s in selected if s.name == "YouTube"} == {"media"}
    with pytest.raises(ValueError, match="无法识别的站点类别"):
        catalog.select_categories(["unknown"])


async def test_category_directory_filter_and_counts(tmp_path):
    async with Application(Settings(data_dir=tmp_path)) as app:
        all_sites = app.catalog.select([])
        username_counts = Counter(site.category for site in all_sites)
        email_counts = Counter(provider.category for provider in EMAIL_PROVIDERS.values())
        result = app.list_capabilities(limit=2, categories=["media", "leisure"], supported_only=True)
        assert result["site_categories"] == [
            {"id": key, "label": label, "username_count": username_counts[key], "email_count": email_counts[key]}
            for key, label in SITE_CATEGORIES.items()
        ]
        assert result["total_sites"] == username_counts["media"] + username_counts["leisure"]
        assert all(site["category"] in {"media", "leisure"} for site in result["sites"])
        assert len(result["email"]["sites"]) == email_counts["media"] + email_counts["leisure"]
        assert all(provider["category"] in {"media", "leisure"} for provider in result["email"]["providers"])
        assert not app.list_capabilities(categories=["other"])["email"]["sites"]
