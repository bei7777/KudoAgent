const $ = (selector, root = document) => root.querySelector(selector);
const state = { boot: null, route: 0, poll: null, taskOffset: 0, taskQuery: "", catalogOffset: 0, catalogQuery: "", catalogRegion: "CN", emailCatalogQuery: "", emailCatalogRegion: "", catalogTab: "username", draft: null, resultViews: new Map() };
const labels = {
  queued: "排队中", running: "研究中", completed: "已完成", failed: "执行失败", interrupted: "已中断", cancelled: "已取消",
  detected: "发现记录", not_detected: "未发现记录", metadata: "资料信息", unknown: "无法判定", blocked: "查询受限", rate_limited: "请求限流", unsupported: "暂不支持", connection_failed: "连接失败", user_confirmed: "用户确认", mixed: "样本结果不同",
  username: "用户名", profile: "公开主页", email: "邮箱", phone: "电话号码", ip: "IP 地址", account: "候选账户", confirmation: "关系确认",
  same_username: "使用相同用户名", links_to: "页面外链", mentions_identifier: "页面提及", pending: "待执行", unreachable: "连接失败",
  registration_check: "邮箱字段校验", account_status: "账户状态", authentication_options: "登录方式查询", public_avatar: "公开头像", public_profile: "公开资料",
  added: "新增记录", disappeared: "不再发现", changed: "内容变化", unverified: "暂时无法核查", not_rechecked: "本次没有查询", unchanged: "保持一致",
};
const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
const budgetLabels = { concurrency: ["同时进行的请求", 1, 50], per_domain: ["每个域名并发", 1, 10], request_timeout: ["单次请求期限（秒）", 1, 300], run_timeout: ["研究期限（秒）", 1, 86400], max_rounds: ["关联分析最多规划轮数", 1, 100], max_requests: ["最多网络请求", 1, 1000000], max_depth: ["关联扩展层数", 0, 5], max_response_bytes: ["单次响应字节上限", 1024, 20000000] };

function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === undefined || value === null || value === false) continue;
    if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (name === "class") node.className = value;
    else if (name === "checked" || name === "disabled") node[name] = value;
    else node.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of [children].flat(Infinity)) {
    if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function showError(error, target = $("#notice")) {
  console.error(error);
  target.hidden = false;
  target.replaceChildren(el("span", {}, error.message || String(error)));
  if (target.id === "notice") target.append(el("button", { class: "text-button", onclick: () => location.reload() }, "重新加载"));
}

function action(fn) {
  return async event => {
    const button = event?.currentTarget;
    if (button instanceof HTMLButtonElement) button.disabled = true;
    try { await fn(event); } catch (error) { showError(error); }
    finally { if (button instanceof HTMLButtonElement) button.disabled = false; }
  };
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json" };
  if (options.method && options.method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-Kudo-Token"] = state.boot.token;
  }
  const response = await fetch(`/api${path}`, { ...options, headers, cache: "no-store", credentials: "same-origin" });
  if (!response.headers.get("content-type")?.includes("application/json")) throw new Error(`服务返回异常（HTTP ${response.status}），请检查运行日志。`);
  const data = await response.json();
  if (!response.ok) {
    const details = data.details?.map(item => `${item.loc[0] === "clues" ? (item.loc[1] ? `第 ${item.loc[1]} 行线索` : "已知线索") : item.loc.join(" · ")}：${item.msg}`).join("\n");
    throw new Error([data.error, details].filter(Boolean).join("\n"));
  }
  return data;
}

function safeLink(url, text = url, attributes = {}) {
  const parsed = new URL(url, location.origin);
  if (!["http:", "https:"].includes(parsed.protocol)) return el("span", { class: "mono" }, text);
  return el("a", { href: parsed.href, target: "_blank", rel: "noopener noreferrer", ...attributes }, text);
}

const status = value => el("span", { class: `status ${value}` }, labels[value] || value);
const date = value => new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
const duration = value => value < 60 ? `${Math.round(value)} 秒` : `${Math.floor(value / 60)} 分 ${Math.round(value % 60)} 秒`;
const heading = (title, subtitle, tools = []) => el("div", { class: "page-heading" }, [el("div", {}, [el("h1", {}, title), el("p", {}, subtitle)]), el("div", { class: "toolbar" }, tools)]);
const button = (text, fn, className = "button") => el("button", { type: "button", class: className, onclick: action(fn) }, text);
const empty = (title, description, controls = []) => el("div", { class: "empty" }, [el("h2", {}, title), el("p", {}, description), el("div", { class: "toolbar" }, controls)]);
const info = pairs => el("dl", { class: "info-list" }, pairs.map(([key, value]) => el("div", {}, [el("dt", {}, key), el("dd", {}, value)])));
const jsonView = value => el("pre", { class: "json-block" }, JSON.stringify(value, null, 2));

function pagination(offset, limit, total, onChange) {
  return el("div", { class: "pagination" }, [el("span", {}, `共 ${total} 项`),
    el("button", { class: "button small", disabled: offset === 0, onclick: action(() => onChange(Math.max(0, offset - limit))) }, "上一页"),
    el("span", {}, `${Math.floor(offset / limit) + 1} / ${Math.max(1, Math.ceil(total / limit))}`),
    el("button", { class: "button small", disabled: offset + limit >= total, onclick: action(() => onChange(offset + limit)) }, "下一页")]);
}

function debounce(fn, delay = 250) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args).catch(showError), delay); };
}

async function showTasks(epoch) {
  const main = $("#main");
  const search = el("input", { type: "search", class: "search", placeholder: "搜索研究目标", "aria-label": "搜索研究目标", value: state.taskQuery });
  const container = el("div");
  main.replaceChildren(el("div", { class: "research-home" }, [
    el("header", { class: "home-heading" }, [el("h1", {}, "从已知线索开始"), el("p", {}, "整理自己或获授权对象的公开足迹，核查每条资料的来源。")]),
    researchComposer(epoch),
    el("section", { class: "history" }, [el("div", { class: "section-heading" }, [el("h2", {}, "研究记录"), search]), container]),
  ]));
  const load = async () => {
    const query = state.taskQuery, offset = state.taskOffset;
    const data = await api(`/research?${new URLSearchParams({ offset, limit: 20, query })}`);
    if (epoch !== state.route || query !== state.taskQuery || offset !== state.taskOffset) return;
    if (!data.total) {
      container.replaceChildren(el("p", { class: "empty-history" }, state.taskQuery ? "没有匹配的研究记录。" : "开始研究后，查询进度、资料和报告会保存在这里。"));
    } else {
      const table = el("table", { class: "task-table" }, [el("thead", {}, el("tr", {}, ["研究线索", "状态", "查询记录", "创建时间"].map(text => el("th", { scope: "col" }, text)))),
        el("tbody", {}, data.items.map(item => el("tr", {}, [
          el("td", {}, [el("a", { class: "task-title", href: `#/research/${item.id}/evidence` }, item.request.identifiers.map(i => i.value).join("、") || item.request.goal), el("small", {}, item.request.goal)]),
          el("td", {}, status(item.status)), el("td", { class: "mono" }, item.evidence_count), el("td", { class: "nowrap muted" }, date(item.created_at)),
        ])))]);
      container.replaceChildren(el("div", { class: "panel table-panel" }, table), pagination(state.taskOffset, 20, data.total, async offset => { state.taskOffset = offset; await load(); }));
    }
    clearTimeout(state.poll);
    if (data.items.some(item => !terminal.has(item.status))) state.poll = setTimeout(() => load().catch(showError), 2500);
  };
  search.addEventListener("input", debounce(async () => { state.taskQuery = search.value; state.taskOffset = 0; await load(); }));
  await load();
}

function citations(run, ids) {
  return ids.map(id => el("a", { class: "citation", href: `#/research/${run}/evidence/${id}` }, `证据 ${id.slice(0, 10)}`));
}

function overview(detail) {
  const completed = (detail.actions.completed || 0) + (detail.actions.unreachable || 0) + (detail.actions.blocked || 0);
  const total = Object.values(detail.actions).reduce((sum, count) => sum + count, 0);
  const events = detail.timeline.flatMap(round => round.source === "catalog" ? [{
    round: round.round + 1, question: `目录检查：${round.actions.length} 项查询`,
    reason: "按完整查询范围分批执行，各站点结果保存在证据记录中", evidence_ids: [],
  }] : round.actions.map(item => ({ ...item, round: round.round + 1 })));
  const timeline = events.length ? el("ol", { class: "timeline" }, events.map(item => el("li", {}, [
    el("small", { class: "mono" }, `第 ${item.round} 轮`), el("p", {}, item.question), el("small", {}, item.reason), el("div", {}, citations(detail.id, item.evidence_ids)),
  ]))) : el("p", { class: "muted" }, terminal.has(detail.status) ? "该任务尚未保存查询计划。" : "Agent 正在读取目标、检查输入并安排查询。完成规划后，查询理由会出现在这里。");
  return el("div", { class: "detail-grid" }, [
    el("section", { class: "panel" }, [el("div", { class: "panel-title" }, [el("h2", {}, "研究过程"), el("span", { class: "muted" }, `${completed} / ${total} 个查询已处理`)]), total > 0 && el("progress", { class: "progress", value: completed, max: total, "aria-label": "已处理的查询数量" }),
      el("p", { class: "coverage-summary" }, `初始范围 ${detail.coverage.planned} 项 · 已返回结果 ${detail.coverage.checked} 项 · 连接失败 ${detail.coverage.failed} 项 · 查询受限 ${detail.coverage.blocked} 项 · 待执行 ${detail.coverage.remaining} 项`), el("div", { class: "section-gap" }, timeline),
      detail.query_failures.length > 0 && el("details", { class: "section-gap query-failures" }, [
        el("summary", {}, `连接失败与查询限制 · ${(detail.actions.unreachable || 0) + (detail.actions.blocked || 0)} 项`),
        el("p", { class: "muted" }, "显示最近 20 项，完整记录包含在报告中。这些查询对应的账户状态保持未知。"),
        ...detail.query_failures.map(item => el("p", {}, [el("strong", {}, `${item.site} · ${labels[item.state]}`), el("span", { class: "mono" }, `　${item.error}`)])),
      ])]),
    el("div", {}, [el("section", { class: "panel" }, [el("h3", {}, "查询范围"), info([
      ["研究标识", el("span", { class: "mono" }, detail.id)], ["创建时间", date(detail.created_at)],
      ["已知线索", detail.request.identifiers.map(item => el("div", {}, [el("small", {}, `${labels[item.kind]}　`), item.value]))],
      ["用户名站点", `${detail.scope.username_sites} 个${!detail.request.sites.length && detail.request.budget.max_sites === null ? "，全部已适配规则" : ""}`],
      ["邮箱查询站点", `${detail.scope.email_sites} 个，按邮箱输入执行`],
      ["请求预算", `${detail.request.budget.max_requests} 次`], ["研究期限", duration(detail.request.budget.run_timeout)],
    ])]), detail.stop_reason && el("section", { class: "panel section-gap" }, [el("h3", {}, "结束原因"), el("p", {}, detail.stop_reason)]), detail.error && el("div", { class: "inline-error section-gap", role: "alert" }, detail.error)]),
  ]);
}

async function loadArtifact(run, metadata, container) {
  const data = await api(`/research/${run}/artifacts/${metadata.artifact_id}`);
  container.replaceChildren(info([["获取时间", date(data.artifact.observed_at)], ["HTTP 状态", String(data.artifact.status_code)], ["内容校验值", el("span", { class: "mono" }, data.artifact.sha256)]]),
    data.binary ? el("p", { class: "hint" }, `二进制资料，${data.bytes} 字节。`) : el("pre", {}, data.text),
    data.truncated && el("p", { class: "hint" }, "显示前 100,000 字节，完整内容可以下载。"),
    el("a", { class: "button small", href: `/api/research/${run}/artifacts/${metadata.artifact_id}?download=true` }, "下载原始资料"));
}

function phoneDetails(item) {
  const data = item.attributes;
  const types = { MOBILE: "移动电话", FIXED_LINE: "固定电话", FIXED_LINE_OR_MOBILE: "固定或移动电话", TOLL_FREE: "免费电话", PREMIUM_RATE: "付费服务号码", VOIP: "网络电话", PERSONAL_NUMBER: "个人号码", UNKNOWN: "类型未知" };
  const formats = { e164: "国际标准格式", international: "国际显示格式", national: "国内显示格式", national_digits: "国内连续号码" };
  const fields = [
    ["标准号码", data.e164], ["国内格式", data.national],
    ["国家 / 地区", [data.country_name, data.region].filter(Boolean).join(" · ")],
    ["号段地区", data.prefix_description], ["号段原始运营商", data.prefix_carrier],
    ["号码类型", types[data.number_type] || data.number_type],
    ["长度规则", typeof data.possible_number === "boolean" ? (data.possible_number ? "符合长度规则" : "未通过长度校验") : null],
    ["分配号段", typeof data.valid_range === "boolean" ? (data.valid_range ? "符合号段规则" : "未通过号段校验") : null],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
  const suggestions = data.search_suggestions || [];
  return el("div", { class: "phone-details" }, [
    info(fields),
    el("p", { class: "hint phone-limits" }, data.mobile_number_portability === true ? "该地区支持携号转网，号段资料可能与当前运营商不同。号码规则不确认当前启用或可接通状态。" : "地区与运营商来自号段资料。号码规则不确认当前启用或可接通状态。"),
    suggestions.length > 0 && el("details", { class: "phone-searches" }, [
      el("summary", {}, `待执行搜索建议 · ${suggestions.length} 项`),
      el("p", { class: "hint" }, "打开链接后在搜索引擎查看结果。这些建议尚未执行，未计入已查询站点。"),
      el("ul", { class: "search-suggestion-list" }, suggestions.map(suggestion => el("li", {}, [
        safeLink(suggestion.url, `${suggestion.engine || new URL(suggestion.url).hostname} · ${formats[suggestion.number_format] || "号码精确搜索"}`),
        el("code", {}, suggestion.query),
      ]))),
    ]),
  ]);
}

function emailProgress(progress) {
  return el("section", { class: "email-progress", "aria-label": "邮箱检查进度", "aria-live": "polite" }, [
    el("p", { class: "email-scope" }, `覆盖 ${progress.site_count} 个站点，计划 ${progress.planned} 项邮箱检查`),
    el("div", { class: "email-counts" }, [
      ["已返回", progress.returned], ["连接失败", progress.connection_failed], ["查询受限", progress.blocked],
      ["执行失败", progress.failed], ["待处理", progress.pending],
    ].map(([title, value]) => el("span", {}, [title, el("strong", {}, value)]))),
    el("p", { class: "hint" }, "每个邮箱分别检查；返回数量表示检查记录数。每条记录保留其实际判定状态。"),
    progress.failures.length > 0 && el("details", {}, [el("summary", {}, "查看未返回结果的站点"),
      el("ul", { class: "email-failures" }, progress.failures.map(item => el("li", {}, [
        el("strong", {}, item.name), status(item.state), el("p", {}, item.error),
      ]))),
    ]),
  ]);
}

function evidenceCard(run, item, artifacts, number) {
  const detail = el("details", {}, [el("summary", {}, "检测规则、关联与原始资料"), el("div", {}, [
    item.detections.length > 0 && el("ul", { class: "record-list" }, item.detections.map(detection => el("li", {}, [status(detection.state), ` ${detection.reason}`, el("div", { class: "mono muted" }, detection.rule_id), detection.signals.length > 0 && el("div", {}, detection.signals.join(" · "))]))),
    item.relations.length > 0 && el("ul", { class: "record-list" }, item.relations.map(relation => el("li", {}, [el("strong", {}, labels[relation.kind]), el("div", {}, `${relation.source} → ${relation.target}`)]))),
    Object.keys(item.attributes).length > 0 && el("details", {}, [el("summary", {}, "提取的资料字段"), jsonView(item.attributes)]),
    item.artifact_ids.map(id => {
      const artifact = artifacts.find(value => value.artifact_id === id);
      const content = el("div", { class: "artifact-block" });
      content.append(button(`查看原始响应 · ${id.slice(0, 10)}`, () => loadArtifact(run, artifact, content), "text-button"));
      return content;
    }),
  ])]);
  return el("article", { class: "panel evidence-card", id: `evidence-${item.evidence_id}` }, [
    el("div", { class: "evidence-heading" }, [el("span", { class: "evidence-number" }, `${String(number).padStart(2, "0")} / ${labels[item.kind]}`), status(item.state), item.attributes.conflict && el("span", { class: "status blocked" }, "规则结论冲突")]),
    el("h3", {}, item.attributes.site || item.attributes.site_id || item.subject),
    el("p", { class: "evidence-summary" }, item.summary),
    item.kind === "phone" && phoneDetails(item),
    item.source_url && safeLink(item.source_url, item.source_url, { class: "source-link" }),
    el("div", { class: "evidence-meta" }, [el("span", {}, date(item.observed_at)),
      ...[...new Set(item.relations.map(relation => relation.kind))].map(kind => el("span", { class: "relation-label" }, labels[kind])),
      el("a", { href: `#/research/${run}/evidence/${item.evidence_id}`, class: "mono" }, `来源 ${item.evidence_id.slice(0, 10)}`)]), detail,
  ]);
}

async function showEvidence(detail, panel, epoch, focusId) {
  let offset = 0, sequence = 0;
  const kinds = new Set(detail.request.identifiers.map(item => item.kind));
  let view = state.resultViews.get(detail.id) || (kinds.has("username") || kinds.has("profile") ? "pages" : kinds.has("email") ? "email" : "metadata");
  let query = "";
  const filters = el("div", { class: "result-filters", "aria-label": "资料分类" });
  const search = el("input", { type: "search", class: "search", placeholder: "搜索站点、线索或资料", "aria-label": "搜索研究资料" });
  const rows = el("div", { class: "evidence-list" }), pager = el("div"), note = el("p", { class: "result-note" }), emailSummary = el("div");
  const notices = {
    pages: "候选页面按来源地址计数。同名仅表示线索，页面外链与用户确认会分别标注。",
    email: "每个站点的检查含义见具体记录。公开头像、公开资料和账户注册状态分别保留。",
    metadata: "电话号码提供号码资料与搜索建议；IP 提供网络及近似地理资料。",
    uncertain: "保留无法判断、查询受限及规则冲突的记录。连接失败详情见执行记录。",
    all: "显示全部采集记录，包括发现、未发现和无法判断的结果。",
  };
  panel.replaceChildren(el("div", { class: "results-toolbar" }, [filters, search]), note, emailSummary, rows, pager);
  const load = async () => {
    const current = ++sequence;
    const data = await api(`/research/${detail.id}/evidence?${new URLSearchParams({ offset, limit: 30, view, query, evidence_id: focusId || "" })}`);
    if (epoch !== state.route || current !== sequence) return;
    const focusedView = document.activeElement?.closest(".result-filters button")?.dataset.view;
    filters.replaceChildren(...[["pages", "候选页面"], ["email", "邮箱检查"], ["metadata", "号码与 IP"], ["uncertain", "待核查"], ["all", "全部记录"]].map(([key, title]) => el("button", {
      type: "button", "data-view": key, class: !focusId && view === key ? "active" : "", "aria-pressed": !focusId && view === key ? "true" : "false",
      onclick: action(async () => { view = key; state.resultViews.set(detail.id, key); offset = 0; focusId = null; await load(); }),
    }, [title, el("span", { class: "tab-count" }, data.summary[key].toLocaleString("zh-CN"))])));
    if (focusedView) $(`[data-view="${focusedView}"]`, filters)?.focus({ preventScroll: true });
    note.replaceChildren(...(focusId ? ["报告引用的来源记录。", button("返回资料列表", async () => { focusId = null; location.hash = `#/research/${detail.id}/evidence`; }, "text-button")] : [notices[view]]));
    const failuresOpen = $("details", emailSummary)?.open;
    emailSummary.replaceChildren(...(view === "email" && !focusId ? [emailProgress(data.email_progress)] : []));
    if (failuresOpen && $("details", emailSummary)) $("details", emailSummary).open = true;
    const existing = new Map([...rows.querySelectorAll(".evidence-card")].map(node => [node.id, node]));
    rows.replaceChildren(...(data.items.length ? data.items.map((item, index) => {
      const number = offset + index + 1;
      const node = existing.get(`evidence-${item.evidence_id}`) || evidenceCard(detail.id, item, data.artifacts, number);
      $(".evidence-number", node).textContent = `${String(number).padStart(2, "0")} / ${labels[item.kind]}`;
      return node;
    }) : [empty(query ? "没有匹配的资料" : "当前没有此类资料", query ? "调整搜索内容，或切换资料分类。" : "已取得的其他查询结果可以在“全部记录”中查看。运行进度和连接问题保存在“执行记录”中。") ]));
    pager.replaceChildren(pagination(offset, 30, data.filtered_total, async value => { offset = value; await load(); rows.scrollIntoView({ block: "start" }); }));
    if (focusId) $(`#evidence-${CSS.escape(focusId)}`)?.scrollIntoView({ block: "start" });
  };
  search.addEventListener("input", debounce(async () => { query = search.value.trim(); offset = 0; focusId = null; await load(); }));
  await load();
  return load;
}

async function showReport(detail, panel, epoch) {
  const data = await api(`/research/${detail.id}/report`);
  if (epoch !== state.route) return;
  const report = data.report;
  panel.replaceChildren(el("article", { class: "report-sheet" }, [
    el("header", { class: "report-heading" }, [el("h2", {}, data.goal), el("div", { class: "toolbar" }, [status(data.status.status), el("small", {}, `${data.evidence.length} 条证据 · ${date(data.status.created_at)}`)])]),
    el("p", { class: "coverage-summary" }, `初始范围 ${data.status.coverage.planned} 项 · 已返回结果 ${data.status.coverage.checked} 项 · 连接失败 ${data.status.coverage.failed} 项 · 查询受限 ${data.status.coverage.blocked} 项 · 待执行 ${data.status.coverage.remaining} 项`),
    report ? report.claims.map((claim, index) => el("section", { class: "claim" }, [el("span", { class: "claim-number" }, String(index + 1).padStart(2, "0")), el("p", {}, claim.text), el("div", {}, citations(detail.id, claim.evidence_ids)), claim.conflicting_evidence_ids.length > 0 && el("div", { class: "conflict" }, ["冲突证据：", ...citations(detail.id, claim.conflicting_evidence_ids)])])) : el("p", { class: "muted" }, "Agent 完成查询与关联分析后生成报告。已取得的资料可以在“发现资料”中查看。"),
    report?.next_actions.length > 0 && el("section", {}, [el("h3", {}, "后续行动"), el("ul", { class: "record-list" }, report.next_actions.map(item => el("li", {}, item)))]),
    el("div", { class: "toolbar section-gap" }, [el("a", { class: "button small", href: `/api/research/${detail.id}/report?format=markdown&download=true` }, "下载 Markdown"), el("a", { class: "button small", href: `/api/research/${detail.id}/report?format=json&download=true` }, "下载 JSON")]),
  ]));
}

async function showConfirmation(detail, panel, epoch) {
  const report = await api(`/research/${detail.id}/report`);
  if (epoch !== state.route) return;
  const urls = [...new Set(report.evidence.flatMap(item => [item.source_url, ...item.relations.map(relation => relation.target)]))].filter(url => /^https?:\/\//.test(url));
  if (!terminal.has(detail.status)) { panel.replaceChildren(empty("研究完成后可以确认关系", "确认声明会作为独立证据保存，并明确标注来自用户。")); return; }
  if (urls.length < 2) { panel.replaceChildren(empty("需要两条已有来源", "研究资料中至少出现两个公开地址后，可以记录你已经确认的关联。")); return; }
  const source = el("select", { required: true, "aria-label": "来源地址" }, urls.map(url => el("option", { value: url }, url)));
  const target = el("select", { required: true, "aria-label": "关联地址" }, urls.map(url => el("option", { value: url }, url)));
  target.selectedIndex = 1;
  const statement = el("textarea", { required: true, maxlength: 4000, rows: 4, "aria-label": "确认声明", placeholder: "写出你已经确认的关系及依据。" });
  const form = el("form", { class: "panel" }, [el("h2", {}, "记录关系确认"), el("p", { class: "muted" }, "你的声明将单独标注为用户确认，并补充到报告中。"), el("label", { class: "field" }, ["来源地址", source]), el("label", { class: "field" }, ["关联地址", target]), el("label", { class: "field" }, ["确认声明", statement]), el("button", { class: "button primary", type: "submit" }, "保存确认")]);
  form.addEventListener("submit", action(async event => { event.preventDefault(); await api(`/research/${detail.id}/confirm`, { method: "POST", body: JSON.stringify({ source: source.value, target: target.value, statement: statement.value }) }); location.hash = `#/research/${detail.id}/report`; }));
  panel.replaceChildren(form);
}

async function showResearch(id, tab, focusId, epoch) {
  const detail = await api(`/research/${id}`);
  if (epoch !== state.route) return;
  const main = $("#main"), summary = el("div", { class: "run-strip", "aria-live": "polite" }), controls = el("div", { class: "toolbar" });
  const panel = el("div"), runNotice = el("div", { class: "run-notice", hidden: true });
  let evidenceCount = -1, previousStatus = "", previousActions = "", refreshEvidence = null;
  main.replaceChildren(el("header", { class: "research-heading" }, [el("a", { class: "back-link", href: "#/tasks" }, "返回研究"),
    heading(detail.request.identifiers.length ? detail.request.identifiers[0].value + (detail.request.identifiers.length > 1 ? ` 等 ${detail.request.identifiers.length} 条线索` : "") : "公开资料研究", detail.request.goal, [controls]),
    el("div", { class: "evidence-meta" }, detail.request.identifiers.map(item => el("span", {}, labels[item.kind])))]), summary, runNotice,
    el("nav", { class: "tabs", "aria-label": "研究内容" }, [["evidence", "发现资料"], ["report", "研究报告"], ["overview", "执行记录"], ["confirm", "确认关系"]].map(([key, text]) => el("a", { href: `#/research/${id}/${key}`, class: tab === key ? "active" : "", "aria-current": tab === key ? "page" : null }, text))), panel);
  const update = async current => {
    if (epoch !== state.route) return;
    const done = current.coverage.checked + current.coverage.failed + current.coverage.blocked;
    summary.replaceChildren(status(current.status), el("span", { class: "run-progress" }, [el("span", {}, `${done.toLocaleString("zh-CN")} / ${current.coverage.planned.toLocaleString("zh-CN")} 项初始查询已处理`),
      current.coverage.planned > 0 && el("progress", { value: done, max: current.coverage.planned, "aria-label": "初始查询进度" })]), el("span", { class: "muted" }, duration(current.elapsed)),
      current.coverage.remaining > 0 && terminal.has(current.status) && el("span", { class: "muted" }, `尚有 ${current.coverage.remaining.toLocaleString("zh-CN")} 项待执行`));
    runNotice.hidden = !current.stop_reason;
    if (current.stop_reason) runNotice.replaceChildren(el("span", {}, current.stop_reason), current.error && el("a", { href: `#/research/${id}/overview` }, "查看执行详情"));
    if (current.status !== previousStatus) {
      controls.replaceChildren();
      if (!terminal.has(current.status)) controls.append(button("取消研究", async () => { await api(`/research/${id}/cancel`, { method: "POST", body: "{}" }); await refresh(); }, "button danger"));
      else if (current.status !== "completed") {
        const retry = el("input", { type: "checkbox", id: "retry-accounts" });
        if (current.uncertain_accounts) controls.append(el("label", { class: "retry-label" }, [retry, `允许重试 ${current.uncertain_accounts} 项未确认的账户请求`]));
        controls.append(button("恢复研究", async () => { await api(`/research/${id}/resume`, { method: "POST", body: JSON.stringify({ retry_interrupted_accounts: retry.checked }) }); await refresh(); }, "button primary"));
      }
      controls.append(button("重新研究", () => showNew(false, current.request), "button small"));
    }
    if (tab === "overview") panel.replaceChildren(overview(current));
    else if (tab === "evidence" && (current.evidence_count !== evidenceCount || current.status !== previousStatus || JSON.stringify(current.actions) !== previousActions)) {
      if (refreshEvidence) await refreshEvidence();
      else refreshEvidence = await showEvidence(current, panel, epoch, focusId);
    }
    else if (tab === "report" && (current.status !== previousStatus || current.evidence_count !== evidenceCount)) await showReport(current, panel, epoch);
    else if (tab === "confirm" && current.status !== previousStatus) await showConfirmation(current, panel, epoch);
    if (epoch !== state.route) return;
    evidenceCount = current.evidence_count; previousStatus = current.status; previousActions = JSON.stringify(current.actions);
    clearTimeout(state.poll);
    if (!terminal.has(current.status)) state.poll = setTimeout(() => refresh().catch(showError), 2000);
  };
  const refresh = async () => update(await api(`/research/${id}`));
  await update(detail);
}

function domesticPlatforms() {
  const states = { available: "用户名查询", restricted: "查询受限", profile_only: "使用主页链接" };
  return el("section", { class: "domestic-platforms", "aria-label": "常用国内平台" }, [
    el("h2", {}, "常用国内平台"),
    el("div", { class: "platform-list" }, state.boot.domestic_platforms.map(platform => el("details", {}, [
      el("summary", {}, [el("span", {}, platform.name), el("small", {}, states[platform.username_state])]),
      el("p", {}, platform.input_hint), platform.note && el("p", { class: "hint" }, platform.note),
      safeLink(platform.url, "打开平台", { class: "platform-link" }),
    ]))),
  ]);
}

async function showCatalog(epoch) {
  const main = $("#main"), content = el("div");
  const tabs = el("div", { class: "tabs" });
  const render = async () => {
    tabs.replaceChildren(...[["username", "用户名站点", state.boot.capabilities.total_sites], ["email", "邮箱查询站点", state.boot.capabilities.email.sites.length]].map(([key, text, count]) => el("button", {
      type: "button", "aria-label": text, "aria-pressed": state.catalogTab === key ? "true" : "false",
      class: state.catalogTab === key ? "active" : "", onclick: action(async () => { state.catalogTab = key; await render(); }),
    }, [text, el("span", { class: "tab-count", "aria-hidden": "true" }, count.toLocaleString("zh-CN"))])));
    if (state.catalogTab === "email") {
      const providers = [...state.boot.capabilities.email.providers].sort((a, b) => Number(b.region === "CN") - Number(a.region === "CN"));
      const input = el("input", { type: "search", class: "search", placeholder: "站点名称或域名", "aria-label": "搜索邮箱站点", value: state.emailCatalogQuery });
      const region = el("select", { "aria-label": "邮箱站点地区" }, [el("option", { value: "" }, "全部站点"), el("option", { value: "CN" }, "中国站点")]);
      region.value = state.emailCatalogRegion;
      const count = el("p", { class: "catalog-result-count", role: "status" }), rows = el("div", { class: "provider-grid" });
      content.replaceChildren(el("div", { class: "catalog-tools" }, [el("p", { class: "muted" }, `研究默认检查全部 ${providers.length} 个邮箱站点，按国内站点优先执行。`), el("div", { class: "catalog-controls" }, [input, region])]), count, rows);
      const filter = () => {
        const terms = state.emailCatalogQuery.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
        const visible = providers.filter(provider => (!state.emailCatalogRegion || provider.region === state.emailCatalogRegion) && terms.every(term => `${provider.name} ${provider.site} ${provider.source}`.toLocaleLowerCase().includes(term)));
        count.textContent = `显示 ${visible.length} / ${providers.length} 个邮箱站点`;
        rows.replaceChildren(...(visible.length ? visible.map(provider => el("article", { class: "panel provider-card", "data-site": provider.site }, [
          el("div", { class: "provider-heading" }, [el("h3", {}, [provider.name, provider.region === "CN" && el("span", { class: "tag" }, "中国")]), status(provider.last_check.state)]),
          el("div", { class: "provider-body" }, [el("span", { class: "tag" }, labels[provider.interaction]), el("p", {}, provider.meaning), safeLink(provider.source, "官方来源 ↗")]),
          el("div", { class: "provider-check" }, [el("span", {}, provider.last_check.observation), el("small", {}, `${provider.last_check.checked_on} · ${provider.last_check.samples} 个自有样本 · 准确率待确认`)]),
        ])) : [empty("未找到对应站点", "可以修改站点名称或切换全部站点浏览。") ]));
      };
      input.addEventListener("input", () => { state.emailCatalogQuery = input.value; filter(); });
      region.addEventListener("change", () => { state.emailCatalogRegion = region.value; filter(); });
      filter();
      return;
    }
    const input = el("input", { type: "search", class: "search", placeholder: "中文名称、英文名称或域名", "aria-label": "搜索站点名称", value: state.catalogQuery });
    const region = el("select", { "aria-label": "站点地区" }, [el("option", { value: "CN" }, `国内站点 · ${state.boot.capabilities.cn_sites}`), el("option", { value: "" }, "全部地区")]);
    region.value = state.catalogRegion;
    const results = el("div", { class: "section-gap" }), description = el("p", { class: "muted" });
    content.replaceChildren(domesticPlatforms(), el("div", { class: "catalog-tools" }, [description, el("div", { class: "catalog-controls" }, [input, region])]), results);
    const load = async () => {
      const query = state.catalogQuery, offset = state.catalogOffset, selectedRegion = state.catalogRegion;
      const data = await api(`/capabilities?${new URLSearchParams({ offset, limit: 30, query, region: selectedRegion })}`);
      if (epoch !== state.route || state.catalogTab !== "username" || query !== state.catalogQuery || offset !== state.catalogOffset || selectedRegion !== state.catalogRegion) return;
      description.textContent = selectedRegion === "CN" ? "当前浏览国内站点，切换全部地区可搜索完整目录。研究会继续执行全球适配规则。" : "当前浏览完整目录。研究优先检查国内站点，再继续执行其余适配规则。";
      const pager = () => pagination(state.catalogOffset, 30, data.total_sites, async offset => { state.catalogOffset = offset; await load(); });
      results.replaceChildren(el("p", { class: "catalog-result-count", role: "status" }, `共 ${data.total_sites.toLocaleString("zh-CN")} 项，当前显示 ${data.total_sites ? offset + 1 : 0}–${offset + data.sites.length} 项；已适配 ${data.supported_sites.toLocaleString("zh-CN")} 项`), pager(),
      data.sites.length ? el("div", { class: "provider-grid" }, data.sites.map(site => el("article", { class: "panel provider-card" }, [
        el("div", { class: "provider-heading" }, [el("h3", {}, site.name), el("span", { class: "tag" }, site.supported ? "规则已适配" : "规则受限")]),
        el("div", { class: "provider-body" }, [site.aliases.length > 0 && el("p", {}, site.aliases.join(" · ")), el("p", { class: "mono" }, site.profile_template), el("p", { class: "site-input-hint" }, site.input_hint), el("small", {}, `规则来源：${site.rules.map(rule => rule.rule_id.split(":")[0]).join(" + ")}`), ...site.rules.filter(rule => rule.unsupported).map(rule => el("p", {}, rule.unsupported))]),
      ]))) : empty("未找到对应站点", "可以使用中文名称、英文名称或域名搜索整个目录。"), pager());
    };
    input.addEventListener("input", debounce(async () => { state.catalogQuery = input.value; state.catalogOffset = 0; await load(); }));
    region.addEventListener("change", action(async () => { state.catalogRegion = region.value; state.catalogOffset = 0; await load(); }));
    await load();
  };
  main.replaceChildren(heading("站点目录", `研究优先检查国内站点，继续执行完整目录中的 ${state.boot.capabilities.supported_sites.toLocaleString("zh-CN")} 个已适配用户名站点。邮箱按已接入站点逐项检查。`), tabs, content);
  await render();
}

async function showCompare(epoch) {
  const runs = [];
  let offset = 0, total;
  do { const page = await api(`/research?limit=100&offset=${offset}`); runs.push(...page.items); total = page.total; offset += 100; } while (offset < total);
  if (epoch !== state.route) return;
  const main = $("#main"), results = el("div");
  if (runs.length < 2) { main.replaceChildren(heading("结果比较", "查看两次研究之间的变化，保留查询范围和失败状态。"), empty("需要至少两次研究记录", "创建或重新执行一项研究后，可以比较新增、内容变化、未复查和暂时无法核查的记录。", [button("新建研究", () => showNew(), "button primary")])); return; }
  const options = () => runs.map(run => el("option", { value: run.id }, `${date(run.created_at)} · ${run.request.goal}`));
  const previous = el("select", { "aria-label": "较早的研究" }, options()), current = el("select", { "aria-label": "较新的研究" }, options());
  previous.selectedIndex = 1;
  const form = el("form", { class: "panel compare-form" }, [el("label", { class: "field" }, ["较早的研究", previous]), el("label", { class: "field" }, ["较新的研究", current]), el("button", { class: "button primary", type: "submit" }, "比较结果")]);
  form.addEventListener("submit", action(async event => {
    event.preventDefault();
    if (previous.value === current.value) throw new Error("请选择两个不同的研究记录。");
    const data = await api(`/compare/${previous.value}/${current.value}`);
    if (epoch !== state.route) return;
    const groups = Object.entries(data.changes).map(([kind, items]) => {
      const rows = items.map(item => {
        const pair = [item.previous, item.current].map((record, index) => el("div", {}, [
          el("small", {}, index ? "较新研究" : "较早研究"),
          record ? [el("p", {}, record.summary), status(record.state),
            el("div", {}, citations(index ? current.value : previous.value, [record.evidence_id]))]
            : el("p", {}, index && item.failure ? `未取得结果：${item.failure}` : "没有对应记录"),
        ]));
        return el("article", { class: "comparison-row" }, [
          el("strong", {}, item.current?.subject || item.previous.subject),
          el("div", { class: "comparison-pair" }, pair),
        ]);
      });
      return el("section", { class: "panel comparison-group" }, [
        el("div", { class: "panel-title" }, [el("h3", {}, labels[kind]), el("span", { class: "mono" }, `${items.length} 项`)]),
        rows.length ? rows : el("span", { class: "muted" }, "没有此类变化"),
      ]);
    });
    results.replaceChildren(...groups);
  }));
  main.replaceChildren(heading("结果比较", "同一次请求的变化逐项对应，查询受限会单独显示。"), form, results);
}

function showSettings() {
  const boot = state.boot, result = el("div", { class: "section-gap", role: "status" });
  $("#main").replaceChildren(heading("运行设置", "使用本机模型配置与研究资料目录。"), el("div", { class: "settings-grid" }, [
    el("section", { class: "panel" }, [el("h2", {}, "模型连接"), info([["状态", boot.model_configured ? "已配置，等待连接检查" : "缺少配置"], ["模型", boot.model || "尚未填写"], ["服务地址", boot.base_url || "尚未填写"], ["密钥变量", boot.api_key_env]]), el("p", { class: "hint section-gap" }, "模型配置从启动时指定的本机文件读取。修改配置后重新启动工作台。"), button("检查模型连接", async () => { result.replaceChildren(el("span", { class: "muted" }, "正在验证模型的工具调用能力…")); const data = await api("/doctor", { method: "POST", body: "{}" }); result.replaceChildren(el("p", {}, "模型连接检查通过。"), jsonView(data)); }), result]),
    el("section", { class: "panel" }, [el("h2", {}, "工作空间"), info([["应用版本", boot.version], ["研究资料", el("span", { class: "mono" }, boot.data_dir)], ["用户名目录", `${boot.capabilities.total_sites} 个站点`], ["邮箱查询站点", `${boot.capabilities.email.sites.length} 个`]]), el("p", { class: "hint section-gap" }, "关闭网页后，运行中的本机服务会继续研究。结束服务会保存中断状态，重新启动后可恢复任务。")]),
  ]));
}

async function route() {
  clearTimeout(state.poll); state.poll = null;
  const epoch = ++state.route;
  $("#notice").hidden = true;
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  const page = parts[0] || "tasks";
  const title = { tasks: "研究任务", research: "研究详情", catalog: "站点目录", compare: "结果比较", settings: "运行设置" }[page];
  if (!title) throw new Error("页面不存在。");
  document.title = `${title} · KudoAgent`;
  document.querySelectorAll("[data-nav]").forEach(link => { if (link.dataset.nav === (page === "research" ? "tasks" : page)) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current"); });
  $("#main").replaceChildren(el("div", { class: "loading" }, "正在读取…"));
  if (page === "tasks") await showTasks(epoch);
  if (page === "research") {
    const tab = parts[2] || "evidence";
    if (!["overview", "evidence", "report", "confirm"].includes(tab)) throw new Error("研究页面不存在。");
    await showResearch(parts[1], tab, parts[3], epoch);
  }
  if (page === "catalog") await showCatalog(epoch);
  if (page === "compare") await showCompare(epoch);
  if (page === "settings") showSettings();
}

function researchComposer(epoch) {
  const draft = state.draft || { clues: "", goal: "", budget: state.boot.budget };
  const clues = el("textarea", { id: "clues", rows: 4, required: true, maxlength: 42000, autocomplete: "off", spellcheck: "false", placeholder: "例如 oschina\n或 https://gitee.com/oschina", "aria-describedby": "clue-help" }, draft.clues);
  const goal = el("textarea", { id: "goal", rows: 2, maxlength: 8000, placeholder: "例如：核查这些主页之间有哪些直接链接。留空时，按线索类型整理公开资料。" }, draft.goal);
  const preview = el("div", { class: "intake-preview", "aria-live": "polite" });
  const error = el("div", { id: "form-error", class: "inline-error", role: "alert", hidden: true });
  const submit = el("button", { id: "submit-research", type: "submit", class: "button primary", disabled: !state.boot.model_configured }, "开始研究");
  const fields = el("div", { id: "budget-fields", class: "budget-grid" }, Object.entries(budgetLabels).map(([key, [label, min, max]]) => el("label", { class: "field" }, [label, el("input", { type: "number", name: key, value: draft.budget[key] ?? state.boot.budget[key], min, max, step: key.endsWith("timeout") ? "any" : 1, required: true })])));
  const guide = el("aside", { class: "input-guide", "aria-label": "线索与查询用途" }, [el("h2", {}, "你提供线索，Agent 查找资料"), info([
    ["用户名", "查找使用相同名称的候选主页"], ["主页链接", "提取公开内容与页面外链"], ["邮箱", "检查各站点账户或公开资料状态"], ["电话号码 / IP", "查询号码资料或网络归属信息"],
  ])]);
  const scope = `国内站点优先，继续执行全部 ${state.boot.capabilities.supported_sites.toLocaleString("zh-CN")} 个适配用户名站点。邮箱使用 ${state.boot.capabilities.email.sites.length} 个接入站点，按线索类型执行。`;
  const form = el("form", { id: "research-form", class: "composer" }, [
    el("div", { class: "composer-layout" }, [el("div", { class: "clue-field" }, [el("div", { class: "field" }, [el("label", { for: "clues" }, "已知线索"), clues]), el("p", { id: "clue-help", class: "hint" }, "每行一条，最多 20 条。中国大陆 11 位手机号可直接填写；国际号码包含 + 与国家代码。数字用户名添加 username: 前缀。"), preview,
      button("填写公开项目示例", async () => { clues.value = "https://gitee.com/oschina"; saveDraft(); await prepare(); clues.focus(); }, "text-button")]), guide]),
    el("details", { class: "research-options", open: Boolean(draft.goal) }, [el("summary", {}, "研究问题与运行选项（选填）"), el("div", { class: "field" }, [el("label", { for: "goal" }, "希望核查的问题"), goal]),
      el("details", { class: "form-section" }, [el("summary", {}, "请求与时间预算"), fields])]), error,
    !state.boot.model_configured && el("p", { class: "inline-error" }, ["请在本机配置模型服务。", el("a", { href: "#/settings" }, "查看运行设置")]),
    el("div", { class: "composer-footer" }, [el("p", { id: "default-scope", class: "hint" }, scope), submit]),
  ]);
  let generation = 0, previewTimer;
  const read = () => ({ clues: clues.value, goal: goal.value.trim(), budget: Object.fromEntries(Object.keys(budgetLabels).map(key => [key, Number($(`[name="${key}"]`, fields).value)])) });
  const saveDraft = () => { state.draft = read(); };
  const prepare = async (values = read()) => {
    if (epoch !== state.route) return null;
    const sequence = ++generation;
    error.hidden = true;
    if (!clues.value.trim()) { preview.replaceChildren(); return null; }
    try {
      const data = await api("/prepare", { method: "POST", body: JSON.stringify(values) });
      if (epoch !== state.route || sequence !== generation) return null;
      preview.replaceChildren(...data.identifiers.map(item => el("span", { class: "clue-chip" }, [el("strong", {}, labels[item.kind]), el("span", {}, item.value)])));
      return data.request;
    } catch (failure) {
      if (epoch === state.route && sequence === generation) { preview.replaceChildren(); error.textContent = failure.message; error.hidden = false; }
      return null;
    }
  };
  clues.addEventListener("input", () => {
    generation++; saveDraft(); clearTimeout(previewTimer);
    previewTimer = setTimeout(() => prepare(), 350);
  });
  form.addEventListener("input", saveDraft);
  form.addEventListener("submit", async event => {
    event.preventDefault(); clearTimeout(previewTimer);
    const values = read();
    const controls = [...form.querySelectorAll("input, textarea, button")].map(node => [node, node.disabled]);
    controls.forEach(([node]) => { node.disabled = true; });
    submit.textContent = "正在创建研究…";
    try {
      const request = await prepare(values);
      if (!request) return;
      const data = await api("/research", { method: "POST", body: JSON.stringify(request) });
      state.draft = null; location.hash = `#/research/${data.run_id}/evidence`;
    } catch (failure) { error.textContent = failure.message; error.hidden = false; }
    finally {
      controls.forEach(([node, disabled]) => { node.disabled = disabled; });
      submit.disabled = !state.boot.model_configured; submit.textContent = "开始研究";
    }
  });
  if (draft.clues) prepare();
  return form;
}

async function showNew(example = false, request = null) {
  state.draft = { clues: request?.identifiers.map(item => item.kind === "username" ? `username:${item.value}` : item.value).join("\n") || (example ? "https://gitee.com/oschina" : ""), goal: request?.goal || "", budget: request?.budget || state.boot.budget };
  if (location.hash === "#/tasks") await route();
  else location.hash = "#/tasks";
}
$(".skip-link").addEventListener("click", event => { event.preventDefault(); $("#main").focus(); $("#main").scrollIntoView({ block: "start" }); });
window.addEventListener("hashchange", () => route().catch(showError));
window.addEventListener("error", event => showError(event.error || new Error(event.message)));
window.addEventListener("unhandledrejection", event => showError(event.reason));

async function boot() {
  state.boot = await api("/bootstrap");
  await route();
}
boot().catch(showError);
