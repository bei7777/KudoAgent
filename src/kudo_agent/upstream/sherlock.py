from typing import Any

from kudo_agent.research_core.models import Detection

# 选自 Sherlock c588d7c8，MIT；版权声明见 licenses/sherlock.txt。
COMMIT = "c588d7c8b476df9f9c84912a05d1430fb8cdcdae"


def interpolate_string(input_object: Any, username: str) -> Any:
    if isinstance(input_object, str):
        return input_object.replace("{}", username)
    if isinstance(input_object, dict):
        return {key: interpolate_string(value, username) for key, value in input_object.items()}
    if isinstance(input_object, list):
        return [interpolate_string(item, username) for item in input_object]
    return input_object


def evaluate(rule_id: str, rule: dict, text: str, status: int) -> Detection:
    methods = rule["errorType"]
    if isinstance(methods, str):
        methods = [methods]
    state = "detected"
    signals = []
    for method in methods:
        if method == "message":
            messages = rule.get("errorMsg", [])
            if isinstance(messages, str):
                messages = [messages]
            matched = [message for message in messages if message in text]
            signals.extend(matched)
            if matched:
                state = "not_detected"
        elif method in {"status_code", "response_url"}:
            codes = rule.get("errorCode", [])
            if isinstance(codes, int):
                codes = [codes]
            if status in codes or not 200 <= status < 300:
                state = "not_detected"
            signals.append(f"HTTP {status}")
        else:
            raise ValueError(f"未知 Sherlock 检测方法：{method}")
    return Detection(rule_id=rule_id, state=state, reason="Sherlock 页面存在性规则", signals=signals)
