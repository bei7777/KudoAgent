import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright, expect


async def main():
    parser = argparse.ArgumentParser(description="通过真实浏览器、模型与公开项目验证 Web 工作台")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if urlsplit(args.url).hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("浏览器验收要求本机工作台地址")
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=args.browser_executable)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        research_posts = []
        page.on("request", lambda request: research_posts.append(request.url)
                if request.method == "POST" and urlsplit(request.url).path == "/api/research" else None)

        async def check_layout(widths=(1440, 1024, 760, 390, 320)):
            for width in widths:
                await page.set_viewport_size({"width": width, "height": 1000})
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
                assert await page.locator("dialog, canvas").count() == 0

        async def open_options():
            options = page.locator(".research-options")
            if not await options.evaluate("node => node.open"):
                await options.locator(":scope > summary").click()

        await page.goto(args.url)
        await expect(page.get_by_role("heading", name="从已知线索开始", exact=True)).to_be_visible()
        initial_url = page.url
        await page.get_by_role("link", name="跳转到主要内容", exact=True).focus()
        await page.keyboard.press("Enter")
        await expect(page.locator("#main")).to_be_focused()
        assert page.url == initial_url
        capabilities = await context.request.get(f"{args.url}/api/capabilities?limit=1")
        assert capabilities.ok
        catalog = await capabilities.json()
        initial_runs = await context.request.get(f"{args.url}/api/research?limit=1")
        initial_total = (await initial_runs.json())["total"]
        clues = page.get_by_label("已知线索", exact=True)
        await expect(clues).to_be_visible()
        await expect(page.locator("#default-scope")).to_contain_text(f"{catalog['supported_sites']:,}")
        assert await page.locator("#site-search, #email-options, [name=max_sites]").count() == 0
        assert await page.get_by_role("combobox", name="线索类型").count() == 0
        await expect(page.get_by_label("希望核查的问题", exact=True)).not_to_be_visible()
        async with page.expect_response(lambda response: urlsplit(response.url).path == "/api/prepare") as prepared_response:
            await clues.fill("sherlock-project\n\nhttps://github.com/sherlock-project\nsherlock-project")
        prepared = await (await prepared_response.value).json()
        assert [item["kind"] for item in prepared["identifiers"]] == ["username", "profile"]
        assert [item["line"] for item in prepared["lines"]] == [1, 3]
        assert prepared["request"]["sites"] == []
        assert prepared["request"]["budget"]["max_sites"] is None
        assert "sherlock-project" in prepared["request"]["goal"]
        await expect(page.locator(".clue-chip")).to_have_count(2)
        await clues.focus()
        await page.keyboard.press("Tab")
        await expect(page.get_by_role("button", name="填写公开项目示例", exact=True)).to_be_focused()
        await page.keyboard.press("Tab")
        await expect(page.locator(".research-options > summary")).to_be_focused()
        await page.keyboard.press("Enter")
        await expect(page.get_by_label("希望核查的问题", exact=True)).to_be_visible()
        await expect(page.get_by_label("希望核查的问题", exact=True)).to_have_value("")
        async with page.expect_response(lambda response: urlsplit(response.url).path == "/api/prepare") as invalid_response:
            await clues.fill("sherlock-project\n\nhttps:/github.com/sherlock-project")
        invalid = await invalid_response.value
        assert invalid.status == 422
        assert (await invalid.json())["details"][0]["loc"][:2] == ["clues", 3]
        await expect(page.locator("#form-error")).to_be_visible()
        await expect(page.locator("#form-error")).to_contain_text("第 3 行线索")
        await expect(page.locator(".clue-chip")).to_have_count(0)
        async with page.expect_response(lambda response: urlsplit(response.url).path == "/api/prepare"):
            await page.get_by_role("button", name="开始研究", exact=True).click()
        await expect(page.get_by_role("heading", name="从已知线索开始", exact=True)).to_be_visible()
        assert not research_posts
        current_runs = await context.request.get(f"{args.url}/api/research?limit=1")
        assert (await current_runs.json())["total"] == initial_total
        await page.get_by_role("button", name="填写公开项目示例", exact=True).click()
        await expect(clues).to_be_focused()
        await expect(page.locator(".clue-chip")).to_have_count(1)
        await expect(page.locator("#form-error")).not_to_be_visible()
        await check_layout()
        await page.set_viewport_size({"width": 1440, "height": 1000})
        print("首页多行解析、去重、逐行校验、默认范围与键盘操作通过", flush=True)

        await page.get_by_role("link", name="站点目录", exact=True).click()
        await expect(page.get_by_role("button", name="用户名站点", exact=True)).to_have_attribute("aria-pressed", "true")
        await expect(page.locator(".catalog-result-count")).to_contain_text("1–30")
        await page.get_by_role("button", name="下一页", exact=True).first.click()
        await expect(page.locator(".catalog-result-count")).to_contain_text("31–60")
        await page.get_by_role("combobox", name="站点地区").select_option("CN")
        for query, name in (("知乎", "Zhihu"), ("豆瓣", "Douban"), ("博客园", "cnblogs.com"), ("哔哩哔哩", "Bilibili")):
            await page.get_by_role("searchbox", name="搜索站点名称").fill(query)
            await expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible()
        await expect(page.get_by_text("该规则需要 哔哩哔哩数字 UID", exact=False)).to_be_visible()
        await page.get_by_role("searchbox", name="搜索站点名称").fill("")
        await page.get_by_role("combobox", name="站点地区").select_option("")
        await page.get_by_role("button", name="邮箱查询站点", exact=True).click()
        await expect(page.get_by_role("heading", name="博客园 中国", exact=True)).to_be_visible()
        await expect(page.get_by_role("heading", name="京东 中国", exact=True)).to_be_visible()
        await check_layout()
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.get_by_role("button", name="用户名站点", exact=True).click()
        await page.get_by_role("searchbox", name="搜索站点名称").fill("GitHub")
        await expect(page.get_by_role("heading", name="GitHub", exact=True)).to_be_visible()
        await expect(page.get_by_role("button", name="用户名站点", exact=True)).to_have_attribute("aria-pressed", "true")
        await page.get_by_role("link", name="运行设置", exact=True).click()
        await page.get_by_role("button", name="检查模型连接", exact=True).click()
        await expect(page.get_by_text("模型连接检查通过。", exact=True)).to_be_visible(timeout=120_000)
        print("站点目录与真实模型连接检查通过", flush=True)

        async def create_research(goal=""):
            await page.get_by_role("link", name="研究", exact=True).click()
            await expect(page.get_by_role("heading", name="从已知线索开始", exact=True)).to_be_visible()
            await page.get_by_label("已知线索", exact=True).fill("https://github.com/sherlock-project")
            await open_options()
            await page.get_by_label("希望核查的问题", exact=True).fill(goal)
            await page.get_by_text("请求与时间预算", exact=True).click()
            await page.locator('[name="max_depth"]').fill("0")
            await page.locator('[name="max_rounds"]').fill("4")
            await page.locator('[name="run_timeout"]').fill("300")
            await page.get_by_label("已知线索", exact=True).fill("https://github.com/sherlock-project")
            await page.get_by_role("button", name="开始研究", exact=False).click()
            await page.wait_for_url("**/#/research/*/evidence", timeout=20_000)
            await expect(page.get_by_role("link", name="发现资料", exact=True)).to_have_attribute("aria-current", "page")
            return page.url.split("/research/", 1)[1].split("/", 1)[0]

        first = await create_research()
        await expect(page.locator(".run-strip .status")).to_have_text("已完成", timeout=320_000)
        print(f"真实研究完成：{first}", flush=True)
        await expect(page.get_by_role("button", name=re.compile(r"^候选页面"))).to_have_attribute("aria-pressed", "true")

        def evidence_response(response, **parameters):
            parsed = urlsplit(response.url)
            query = parse_qs(parsed.query, keep_blank_values=True)
            return parsed.path == f"/api/research/{first}/evidence" and all(
                query.get(key) == [value] for key, value in parameters.items()
            )

        for view, title in (("email", "邮箱检查"), ("metadata", "号码与 IP"), ("uncertain", "待核查"),
                            ("all", "全部记录"), ("pages", "候选页面")):
            control = page.get_by_role("button", name=re.compile(f"^{title}"))
            async with page.expect_response(lambda response: evidence_response(response, view=view)) as selected_response:
                await control.click()
            selected = await (await selected_response.value).json()
            await expect(control).to_have_attribute("aria-pressed", "true")
            await expect(control.locator(".tab-count")).to_have_text(f"{selected['summary'][view]:,}")
            await expect(page.locator(".evidence-card")).to_have_count(len(selected["items"]))
            if view == "pages":
                assert selected["items"]
                assert all(item["kind"] in {"profile", "account"} and item["state"] == "detected"
                           for item in selected["items"])
        search = page.get_by_role("searchbox", name="搜索研究资料", exact=True)
        async with page.expect_response(lambda response: evidence_response(response, query="github.com")) as searched_response:
            await search.fill("github.com")
        searched = await (await searched_response.value).json()
        assert searched["filtered_total"] > 0
        await expect(page.locator(".evidence-card")).to_have_count(len(searched["items"]))
        async with page.expect_response(lambda response: evidence_response(response, query="urn:kudoagent:no-matching-source")) as empty_response:
            await search.fill("urn:kudoagent:no-matching-source")
        assert (await (await empty_response.value).json())["filtered_total"] == 0
        await expect(page.get_by_role("heading", name="没有匹配的资料", exact=True)).to_be_visible()
        async with page.expect_response(lambda response: evidence_response(response, query="")):
            await search.fill("")
        await expect(page.locator(".evidence-card").first).to_be_visible()
        await page.get_by_text("检测规则、关联与原始资料", exact=True).first.click()
        await page.get_by_role("button", name="查看原始响应", exact=False).first.click()
        await expect(page.get_by_text("内容校验值", exact=True).first).to_be_visible()
        report_response = await context.request.get(f"{args.url}/api/research/{first}/report")
        report = await report_response.json()
        assert report["status"]["coverage"] == {"planned": 1, "checked": 1, "failed": 0, "blocked": 0, "remaining": 0, "complete": True}
        detail = await context.request.get(f"{args.url}/api/research/{first}")
        stored = await detail.json()
        assert stored["request"]["sites"] == []
        assert stored["request"]["budget"]["max_sites"] is None
        assert len(stored["request"]["email_sites"]) == len(catalog["email"]["sites"])
        assert "提取已知主页" in stored["request"]["goal"]
        assert stored["scope"]["username_sites"] == catalog["supported_sites"]
        assert report["report"]["claims"]
        evidence_ids = {item["evidence_id"] for item in report["evidence"]}
        assert all(claim["evidence_ids"] and set(claim["evidence_ids"]).issubset(evidence_ids)
                   and set(claim["conflicting_evidence_ids"]).issubset(evidence_ids)
                   for claim in report["report"]["claims"])
        metadata = report["artifacts"][0]
        raw = await context.request.get(f"{args.url}/api/research/{first}/artifacts/{metadata['artifact_id']}?download=true")
        assert hashlib.sha256(await raw.body()).hexdigest() == metadata["sha256"]
        await page.get_by_role("link", name="研究报告", exact=True).click()
        await expect(page.locator(".claim").first).to_be_visible()
        await check_layout()
        await page.set_viewport_size({"width": 1440, "height": 1000})
        async with page.expect_download() as download_info:
            await page.get_by_role("link", name="下载 Markdown", exact=True).click()
        download = await download_info.value
        args.output.parent.mkdir(parents=True, exist_ok=True)
        await download.save_as(args.output.parent / "browser-report.md")
        citation = page.locator(".citation").first
        cited_id = (await citation.get_attribute("href")).rsplit("/", 1)[1]
        await citation.click()
        await expect(page.locator(f"#evidence-{cited_id}")).to_be_visible()
        await expect(page.locator(".evidence-card")).to_have_count(1)
        await page.get_by_role("button", name="返回资料列表", exact=True).click()
        await expect(page.get_by_role("button", name=re.compile(r"^候选页面"))).to_have_attribute("aria-pressed", "true")
        await page.get_by_role("link", name="执行记录", exact=True).click()
        await expect(page.get_by_role("heading", name="研究过程", exact=True)).to_be_visible()
        await check_layout()
        await page.set_viewport_size({"width": 1440, "height": 1000})
        print("候选分类、搜索、原始资料校验、报告下载与引用定位通过", flush=True)

        second = await create_research("再次核查 Sherlock 项目在 GitHub 的公开主页，记录当前查询结果与来源。")
        await page.get_by_role("button", name="取消研究", exact=True).click()
        await expect(page.locator(".run-strip .status")).to_have_text("已取消", timeout=30_000)
        await page.get_by_role("button", name="恢复研究", exact=True).click()
        await expect(page.locator(".run-strip .status")).to_have_text("已完成", timeout=320_000)
        print(f"真实任务取消与恢复通过：{second}", flush=True)
        await page.get_by_role("link", name="结果比较", exact=True).click()
        await page.get_by_label("较早的研究", exact=True).select_option(first)
        await page.get_by_label("较新的研究", exact=True).select_option(second)
        await page.get_by_role("button", name="比较结果", exact=True).click()
        await expect(page.get_by_role("heading", name="保持一致", exact=True)).to_be_visible()
        await check_layout()
        await page.get_by_role("link", name="研究", exact=True).click()
        await expect(page.get_by_role("heading", name="从已知线索开始", exact=True)).to_be_visible()
        await expect(page.locator(f'.task-title[href="#/research/{second}/evidence"]')).to_be_visible()
        await check_layout()
        assert len(research_posts) == 2
        assert not errors, errors
        result = {"first_run": first, "second_run": second, "model": "真实配置服务", "browser_errors": errors,
                  "checks": ["多行线索解析", "线索去重", "逐行格式错误", "预览不创建任务", "可选研究问题", "目录分页", "中文搜索", "国内站点筛选", "默认全部规则", "键盘表单操作", "模型连接", "研究创建", "覆盖进度", "候选资料分类", "资料搜索", "原始资料校验", "报告下载", "引用定位", "执行记录", "取消", "恢复", "比较", "320至1440像素布局"]}
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        await browser.close()
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
