import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from kudo_agent.application.config import Settings
from kudo_agent.application.intake import ResearchIntake
from kudo_agent.integrations.web import create_web_app
from kudo_agent.research_core.models import EMAIL_SITES, Budget, ResearchRequest


def test_intake_recognizes_and_normalizes_real_identifier_formats():
    prepared = ResearchIntake.model_validate({"clues": (
        "sherlock-project\nhttps://github.com/sherlock-project\nsupport@github.com\n"
        "+41 44 668 18 00\n1.1.1.1\n2606:4700:4700::1111"
    )}).prepare()
    assert [item["kind"] for item in prepared["identifiers"]] == [
        "username", "profile", "email", "phone", "ip", "ip",
    ]
    assert prepared["identifiers"][3]["value"] == "+41446681800"
    request = ResearchRequest.model_validate(prepared["request"])
    assert request.sites == []
    assert request.budget == Budget()
    assert request.email_sites == list(EMAIL_SITES)
    assert "查找对应用户名的公开账号页面" in request.goal
    assert "注册或公开资料迹象" in request.goal


def test_intake_preserves_physical_lines_and_deduplicates():
    prepared = ResearchIntake.model_validate({
        "clues": "\n sherlock-project \n\nhttps://github.com/sherlock-project\nsherlock-project\n",
        "goal": "  核对项目介绍中的公开关联  ",
        "budget": {"max_depth": 0},
    }).prepare()
    assert [item["line"] for item in prepared["lines"]] == [2, 4]
    assert prepared["request"]["goal"] == "核对项目介绍中的公开关联"
    assert prepared["request"]["budget"]["max_depth"] == 0


@pytest.mark.parametrize("value", [
    "https:/github.com/sherlock-project", "www.github.com/sherlock-project", "https://", "https:////github.com",
    "https://owner:password@github.com/", "file:///etc/passwd", "owner@@github.com",
    "owner@", "+12345", "+", "+abc", "999.1.1.1", "127.0.0.1", "::1", "研究 某个账号",
])
def test_intake_rejects_invalid_formats_on_their_original_line(value):
    with pytest.raises(ValidationError) as caught:
        ResearchIntake.model_validate({"clues": f"sherlock-project\n\n{value}"})
    assert caught.value.errors()[0]["loc"][:2] == ("clues", 3)


def test_prepare_api_is_local_and_does_not_create_research(tmp_path):
    with TestClient(create_web_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        assert client.post("/api/prepare", json={"clues": "sherlock-project"}).status_code == 403
        token = client.get("/api/bootstrap").json()["token"]
        client.headers["X-Kudo-Token"] = token
        response = client.post("/api/prepare", json={"clues": "sherlock-project"})
        assert response.status_code == 200
        assert response.json()["request"]["budget"]["max_sites"] is None
        assert response.json()["identifiers"] == [{"kind": "username", "value": "sherlock-project"}]
        assert client.get("/api/research").json()["total"] == 0
        invalid = client.post("/api/prepare", json={"clues": "sherlock-project\n\nowner@"})
        assert invalid.status_code == 422
        assert invalid.json()["details"][0]["loc"][:2] == ["clues", 3]
        assert "owner@" not in invalid.text
        assert client.post("/api/prepare", json={"clues": " \n "}).status_code == 422


def test_intake_preserves_chinese_and_numeric_usernames():
    prepared = ResearchIntake.model_validate({"clues": "开源中国\n123456"}).prepare()
    assert prepared["identifiers"] == [
        {"kind": "username", "value": "开源中国"}, {"kind": "username", "value": "123456"},
    ]
