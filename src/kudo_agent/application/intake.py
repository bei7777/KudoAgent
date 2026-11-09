from typing import Annotated

import phonenumbers
from pydantic import BeforeValidator, Field, HttpUrl, TypeAdapter, field_validator

from kudo_agent.research_core.models import Budget, Identifier, Model, ResearchRequest

PROFILE_URL = TypeAdapter(HttpUrl)


def identify_clue(value: str) -> Identifier:
    lowered = value.casefold()
    if lowered.startswith("username:"):
        return Identifier(kind="username", value=value.partition(":")[2].strip())
    if "://" in value or lowered.startswith(("http:", "https:", "www.")) or "/" in value or "\\" in value:
        if not lowered.startswith(("http://", "https://")):
            raise ValueError("主页链接必须包含完整的 http:// 或 https:// 地址")
        identifier = Identifier(kind="profile", value=value)
        url = PROFILE_URL.validate_python(identifier.value)
        return Identifier(kind="profile", value=str(url))
    if "@" in value:
        return Identifier(kind="email", value=value)
    if value.startswith("+"):
        if not phonenumbers.is_possible_number_string(value, None):
            raise ValueError("请填写包含 +、国家代码与完整号码的国际电话号码")
        number = phonenumbers.parse(value, None)
        if not phonenumbers.is_valid_number(number):
            raise ValueError("电话号码无效，请检查国家代码、号段和号码长度")
        return Identifier(kind="phone", value=value)
    if all(char.isdecimal() or char.isspace() or char == "-" for char in value):
        digits = phonenumbers.normalize_digits_only(value)
        if len(digits) == 11 or (len(digits) == 13 and digits.startswith("86")):
            number = phonenumbers.parse(value, "CN")
            if (number.country_code != 86 or not phonenumbers.is_valid_number(number)
                    or phonenumbers.number_type(number) != phonenumbers.PhoneNumberType.MOBILE):
                raise ValueError(
                    "中国大陆手机号无效，请检查 11 位号码及号段；国际号码请添加 + 和国家代码；"
                    "数字用户名可添加 username: 前缀"
                )
            return Identifier(
                kind="phone", value=phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
            )
    if ":" in value or (value[:1].isdigit() and value.count(".") == 3):
        return Identifier(kind="ip", value=value)
    return Identifier(kind="username", value=value)


Clue = Annotated[Identifier, BeforeValidator(identify_clue)]


class ResearchIntake(Model):
    clues: dict[int, Clue] = Field(min_length=1)
    goal: str = Field(default="", max_length=8000)
    budget: Budget = Field(default_factory=Budget)

    @field_validator("clues", mode="before", json_schema_input_type=str)
    @classmethod
    def split_lines(cls, value):
        if not isinstance(value, str):
            raise ValueError("请填写文本，每行提供一条已知线索")
        if len(value) > 50_000:
            raise ValueError("线索文本长度不能超过 50,000 个字符")
        lines = {number: line.strip() for number, line in enumerate(value.splitlines(), start=1) if line.strip()}
        if not lines:
            raise ValueError("请至少填写一条用户名、主页、邮箱、手机号码、国际电话号码或公共 IP")
        return lines

    @field_validator("clues", mode="after")
    @classmethod
    def unique_lines(cls, value):
        unique, seen = {}, set()
        for number, identifier in value.items():
            key = (identifier.kind, identifier.value)
            if key not in seen:
                unique[number] = identifier
                seen.add(key)
        if len(unique) > 20:
            raise ValueError("每项研究最多填写 20 条不同的线索")
        return unique

    @field_validator("goal")
    @classmethod
    def trim_goal(cls, value):
        return value.strip()

    def prepare(self) -> dict:
        identifiers = list(self.clues.values())
        tasks = {
            "username": "查找对应用户名的公开账号页面",
            "profile": "提取已知主页的公开资料及页面外链",
            "email": "检查邮箱在已接入站点的注册或公开资料迹象",
            "phone": "整理电话号码的号段资料与公开搜索建议",
            "ip": "查询 IP 的网络归属与地理资料",
        }
        kinds = dict.fromkeys(identifier.kind for identifier in identifiers)
        subject = "、".join(identifier.value[:80] for identifier in identifiers[:3])
        if len(identifiers) > 3:
            subject += f"等 {len(identifiers)} 条线索"
        goal = self.goal or (
            f"整理 {subject} 对应的公开资料：" + "；".join(tasks[kind] for kind in kinds)
            + "。逐项引用来源并说明查询限制；同名仅作为候选关联。"
        )
        request = ResearchRequest(goal=goal, identifiers=identifiers, budget=self.budget)
        return {
            "request": request.model_dump(),
            "identifiers": [identifier.model_dump() for identifier in identifiers],
            "lines": [{"line": number, **identifier.model_dump()} for number, identifier in self.clues.items()],
        }
