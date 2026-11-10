from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kudo_agent.research_core.email import EmailCollector, EmailObservation


async def archive(collector: "EmailCollector") -> "EmailObservation":
    session = await collector.fetch("https://archive.org/services/csrf-token")
    if result := collector.http_result(session):
        return result
    configuration = collector.json_object(session)
    if configuration.get("success") is not True:
        raise ValueError("Internet Archive 未返回有效的邮箱检查会话")
    token = configuration["value"]["token"]
    if not isinstance(token, str) or not token:
        raise ValueError("Internet Archive 邮箱检查缺少 CSRF 令牌")
    response = await collector.fetch(
        "https://archive.org/services/account/signup/", method="POST",
        headers={"Referer": "https://archive.org/signup", "X-CSRF-Token": token},
        json_body={"field": "username", "value": collector.email, "op": "check", "t": token},
    )
    if result := collector.http_result(response):
        return result
    payload = collector.json_object(response)
    if type(payload.get("success")) is not bool:
        raise ValueError("Internet Archive 字段检查缺少明确的 success 状态")
    if payload["success"]:
        return collector.result("not_detected", "邮箱字段校验通过，接口允许继续注册", response)
    return collector.result("unknown", "邮箱字段未通过校验，注册状态尚未确认", response)


HANDLERS = {"archive": archive}
