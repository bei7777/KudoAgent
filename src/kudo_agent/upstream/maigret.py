import copy
import re
from dataclasses import dataclass
from typing import Any

from kudo_agent.research_core.models import Detection

# 选自 Maigret 21577cf7，MIT；版权声明见 licenses/maigret.txt。
COMMIT = "21577cf7730ddaad0f17d7797e6dbf4642411b50"


@dataclass
class MaigretEngine:
    name: str
    data: dict[str, Any]


class MaigretSite:
    def __init__(self, name: str, information: dict, engine: MaigretEngine | None = None):
        self.name = name
        self.data = copy.deepcopy(information)
        for key, value in (engine.data.get("site", {}) if engine else {}).items():
            if isinstance(value, dict):
                self.data.setdefault(key, {}).update(copy.deepcopy(value))
            elif isinstance(value, list):
                self.data[key] = self.data.get(key, []) + copy.deepcopy(value)
            else:
                self.data.setdefault(key, value)
        self.data.setdefault("checkType", "status_code")
        self.data.setdefault("alexaRank", 2**63 - 1)
        for key in ("url", "urlProbe"):
            if key in self.data:
                for field in ("urlMain", "urlSubpath"):
                    self.data[key] = self.data[key].replace("{" + field + "}", self.data.get(field, ""))

    def extract_id_from_url(self, url: str) -> str | None:
        template = self.data.get("url", "")
        if template.count("{username}") != 1:
            return None
        pattern = make_profile_url_regexp(template)
        match = pattern.fullmatch(url.rstrip("/"))
        return match.group("_maigret_username") if match else None


def make_profile_url_regexp(url: str):
    # 保留上游模板转正则的方式，完整匹配限制资料页边界。
    parts = url.rstrip("/").split("{username}")
    pattern = r"(?P<_maigret_username>[^/?#]+)".join(re.escape(part) for part in parts)
    return re.compile(pattern, re.IGNORECASE)


def load_sites(database: dict) -> list[MaigretSite]:
    engines = {name: MaigretEngine(name, data) for name, data in database["engines"].items()}
    return [
        MaigretSite(name, data, engines[data["engine"]] if data.get("engine") else None)
        for name, data in database["sites"].items()
    ]


def is_plausible_username(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    value = value.strip()
    return bool(value) and "://" not in value and "/" not in value and not any(
        char.isspace() for char in value
    ) and not ("@" in value and "." in value)


ERROR_MARKERS = (
    "<title>Attention Required! | Cloudflare</title>",
    "<title>Just a moment",
    "Please stand by, while we are checking your browser",
    "Incapsula incident ID",
    "<title>Client Challenge</title>",
    "<title>DDoS-Guard</title>",
    "/cdn-cgi/challenge-platform/",
    "SlardarWAF",
    "/.within.website/x/",
    "window.POW_CHALLENGE_DATA",
    "<title>Vercel Security Checkpoint</title>",
    "unusual traffic from your computer network",
    "google.com/recaptcha/challengepage/",
)


def detect_error_page(text: str, status: int, fail_flags: dict | None = None,
                      expected_statuses: tuple[int, ...] = ()) -> str | None:
    if status == 429:
        return "rate_limited"
    if status in {401, 403, 999} or any(flag in text for flag in ERROR_MARKERS):
        return "blocked"
    if status >= 500 or any(flag in text for flag in (fail_flags or {})):
        return "unknown"
    if not 200 <= status < 300 and status not in {404, 410, *expected_statuses}:
        return "unknown"
    return None


def process_site_result(rule_id: str, rule: dict, username: str, text: str, status: int):
    presence_flags = rule.get("presenseStrs", [])
    absence_flags = rule.get("absenceStrs", [])
    present = any(flag in text for flag in presence_flags) if presence_flags else bool(text)
    if not presence_flags and any(ord(char) > 127 for char in username):
        present = username in text
    absent = any(flag in text for flag in absence_flags)
    method = rule.get("checkType", "status_code")
    signals = [flag for flag in presence_flags + absence_flags if flag in text]
    if present and absent and presence_flags:
        return Detection(rule_id=rule_id, state="unknown", reason="正反证据同时出现", signals=signals)
    if method == "message":
        found = present and not absent
    elif method == "status_code":
        found = 200 <= status < 300
    elif method == "response_url":
        found = 200 <= status < 300 and present
    else:
        raise ValueError(f"未知 Maigret 检测方法：{method}")
    return Detection(
        rule_id=rule_id, state="detected" if found else "not_detected",
        reason="Maigret 页面存在性规则", signals=signals + [f"HTTP {status}"],
    )
