const $ = (selector, root = document) => root.querySelector(selector);
const state = { boot: null, route: 0, poll: null, taskOffset: 0, taskQuery: "", catalogOffset: 0, catalogQuery: "", catalogRegion: "CN", emailCatalogQuery: "", emailCatalogRegion: "", catalogTab: "username", draft: null, resultViews: new Map() };
const labels = {
  queued: "排队中", running: "研究中", completed: "已完成", partial: "部分完成", failed: "执行失败", interrupted: "已中断", cancelled: "已取消",
  detected: "发现记录", not_detected: "未发现记录", metadata: "资料信息", unknown: "无法判定", blocked: "查询受限", rate_limited: "请求限流", unsupported: "暂不支持", connection_failed: "连接失败", user_confirmed: "用户确认", mixed: "样本结果不同",
  username: "用户名", profile: "公开主页", email: "邮箱", phone: "电话号码", ip: "IP 地址", account: "候选账户", confirmation: "关系确认",
  same_username: "使用相同用户名", links_to: "页面外链", mentions_identifier: "页面提及", pending: "待执行", unreachable: "连接失败",
  registration_check: "邮箱字段校验", account_status: "账户状态", authentication_options: "登录方式查询", public_avatar: "公开头像", public_profile: "公开资料",
  added: "新增记录", disappeared: "不再发现", changed: "内容变化", unverified: "暂时无法核查", not_rechecked: "本次没有查询", unchanged: "保持一致",
};
const terminal = new Set(["completed", "partial", "failed", "cancelled", "interrupted"]);
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
    const error = new Error([data.error, details].filter(Boolean).join("\n"));
    error.scopeField = data.scope_field;
    throw error;
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
const heading = (title, subtitle, tools = []) => el("div", { class: "page-heading" }, [el("div", {}, [el("h1", {}, title), subtitle && el("p", {}, subtitle)]), tools.length > 0 && el("div", { class: "toolbar" }, tools)]);
const button = (text, fn, className = "button") => el("button", { type: "button", class: className, onclick: action(fn) }, text);
const empty = (title, description, controls = []) => el("div", { class: "empty" }, [el("h2", {}, title), description && el("p", {}, description), controls.length > 0 && el("div", { class: "toolbar" }, controls)]);
const info = pairs => el("dl", { class: "info-list" }, pairs.map(([key, value]) => el("div", {}, [el("dt", {}, key), el("dd", {}, value)])));
const jsonView = value => el("pre", { class: "json-block" }, JSON.stringify(value, null, 2));

function pagination(offset, limit, total, onChange) {
  if (offset === 0 && total <= limit) return document.createDocumentFragment();
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
  const history = el("section", { class: "history", hidden: true }, [el("div", { class: "section-heading" }, [el("h2", {}, "研究记录"), search]), container]);
  main.replaceChildren(el("div", { class: "research-home" }, [
    el("h1", { class: "home-heading" }, "新建研究"), researchComposer(epoch), history,
  ]));
  const load = async () => {
    const query = state.taskQuery, offset = state.taskOffset;
    const data = await api(`/research?${new URLSearchParams({ offset, limit: 20, query, brief: true })}`);
    if (epoch !== state.route || query !== state.taskQuery || offset !== state.taskOffset) return;
    history.hidden = !data.total && !query;
    if (!data.total) {
      container.replaceChildren(el("p", { class: "empty-history" }, "没有匹配的研究记录。"));
    } else {
      const table = el("table", { class: "task-table" }, [el("thead", {}, el("tr", {}, ["研究线索", "状态", "证据数量", "创建时间"].map(text => el("th", { scope: "col" }, text)))),
        el("tbody", {}, data.items.map(item => el("tr", {}, [
          el("td", {}, [el("a", { class: "task-title", href: `#/research/${item.id}/evidence` }, item.request.identifiers.map(i => i.value).join("、") || item.request.goal), item.request.identifiers.length > 0 && el("small", {}, item.request.goal)]),
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
  const kinds = new Set(detail.request.identifiers.map(item => item.kind));
  const relatedSites = kinds.has("profile") && detail.request.budget.max_depth > 0;
  const completed = (detail.actions.completed || 0) + (detail.actions.unreachable || 0) + (detail.actions.blocked || 0);
  const total = Object.values(detail.actions).reduce((sum, count) => sum + count, 0);
  const decisions = detail.timeline.flatMap(round =>
    round.actions.map(item => el("li", {}, [
      el("small", { class: "mono" }, `第 ${round.round + 1} 轮`), el("p", {}, item.question),
      el("small", {}, item.reason), item.evidence_ids.length > 0 && el("div", {}, citations(detail.id, item.evidence_ids)),
    ])));
  const timeline = detail.catalog_batches || decisions.length ? el("ol", { class: "timeline" }, [
    detail.catalog_batches > 0 && el("li", {}, `目录查询分 ${detail.catalog_batches} 批安排，共 ${detail.catalog_queries} 项。`),
    ...decisions,
  ]) : el("p", { class: "muted" }, terminal.has(detail.status) ? "尚无查询计划。" : "正在准备查询。");
  return el("div", { class: "detail-grid" }, [
    el("section", { class: "panel" }, [el("div", { class: "panel-title" }, [el("h2", {}, "研究过程"), total > detail.coverage.planned && el("span", { class: "muted" }, `含后续查询，已处理 ${completed} / ${total} 项`)]),
      el("p", { class: "coverage-summary" }, `初始范围 ${detail.coverage.planned} 项 · 已返回结果 ${detail.coverage.checked} 项 · 连接失败 ${detail.coverage.failed} 项 · 查询受限 ${detail.coverage.blocked} 项 · 待执行 ${detail.coverage.remaining} 项`), el("div", { class: "section-gap" }, timeline),
      detail.query_failures.length > 0 && el("details", { class: "section-gap query-failures" }, [
        el("summary", {}, `连接失败与查询限制 · ${(detail.actions.unreachable || 0) + (detail.actions.blocked || 0)} 项`),
        el("p", { class: "muted" }, "显示最近 20 项，完整记录包含在报告中。这些查询对应的账户状态保持未知。"),
        ...detail.query_failures.map(item => el("p", {}, [el("strong", {}, `${item.site} · ${labels[item.state]}`), el("span", { class: "mono" }, `　${item.error}`)])),
      ])]),
    el("div", {}, [el("section", { class: "panel" }, [el("h3", {}, "查询范围"), info([
      ["研究标识", el("span", { class: "mono" }, detail.id)], ["创建时间", date(detail.created_at)],
      ["已知线索", detail.request.identifiers.map(item => el("div", {}, [el("small", {}, `${labels[item.kind]}　`), item.value]))],
      ...(detail.request.goal_sites?.length ? [["研究问题指定的平台", detail.request.goal_sites.join("、")]] : []),
      ...((kinds.has("username") || kinds.has("email") || relatedSites) && detail.request.site_categories?.length ? [["站点类别", state.boot.capabilities.site_categories
        .filter(item => detail.request.site_categories.includes(item.id)).map(item => item.label).join("、")]] : []),
      ...(kinds.has("username") || relatedSites ? [[kinds.has("username") ? "用户名站点" : detail.request.follow_profile_links ? "外链可查询站点" : "关联用户名站点", `${detail.scope.username_sites} 个`]] : []),
      ...(kinds.has("email") ? [["邮箱查询站点", `${detail.scope.email_sites} 个`]] : []),
      ...(kinds.size === 1 && kinds.has("phone") ? [] : [["请求预算", `${detail.request.budget.max_requests} 次`]]),
      ["研究期限", duration(detail.request.budget.run_timeout)],
    ])]), detail.error && el("div", { class: "inline-error section-gap", role: "alert" }, detail.error)]),
  ]);
}

async function loadArtifact(run, metadata, container, showRequest = false) {
  const data = await api(`/research/${run}/artifacts/${metadata.artifact_id}`);
  const requestFields = showRequest ? [["检测请求", el("code", {}, `${data.artifact.method} ${data.artifact.url}`)]] : [];
  container.replaceChildren(info([...requestFields, ["获取时间", date(data.artifact.observed_at)], ["HTTP 状态", String(data.artifact.status_code)], ["内容校验值", el("span", { class: "mono" }, data.artifact.sha256)]]),
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
    progress.failures.length > 0 && el("details", {}, [el("summary", {}, "查看未返回结果的站点"),
      el("ul", { class: "email-failures" }, progress.failures.map(item => el("li", {}, [
        el("strong", {}, item.name), status(item.state), el("p", {}, item.error),
      ]))),
    ]),
  ]);
}

function emailCheckEvidence(run, item, responses) {
  if (!responses.length) return el("p", { class: "hint" }, "此记录没有保存原始响应。");
  const select = el("select", { "aria-label": "已保存的响应" }, responses.map((record, index) =>
    el("option", { value: index }, `响应 ${index + 1} · ${record.method} · HTTP ${record.status_code}`)));
  const observed = responses.findLastIndex(record => record.url === item.source_url);
  if (observed >= 0) select.selectedIndex = observed;
  const body = el("div", { class: "artifact-block" });
  const details = el("details", { class: "email-check-evidence" }, [
    el("summary", {}, "查看检测依据"), responses.length > 1 && select, body,
  ]);
  function openResponse() {
    const record = responses[select.selectedIndex];
    if (body.firstElementChild?.dataset.artifact === record.artifact_id) return;
    const content = el("div", { "data-artifact": record.artifact_id }, "正在读取已保存的响应…");
    body.replaceChildren(content);
    loadArtifact(run, record, content, true).catch(error => {
      delete content.dataset.artifact;
      showError(error, content);
    });
  }
  details.addEventListener("toggle", () => { if (details.open) openResponse(); });
  select.addEventListener("change", openResponse);
  return details;
}

function evidenceCard(run, item, artifacts) {
  const email = item.kind === "email";
  const responses = item.artifact_ids.map(id => artifacts.find(record => record.artifact_id === id));
  const detail = el("details", {}, [el("summary", {}, email ? "检查详情" : "检测规则、关联与原始资料"), el("div", {}, [
    item.detections.length > 0 && el("ul", { class: "record-list" }, item.detections.map(detection => el("li", {}, [status(detection.state), ` ${detection.reason}`, el("div", { class: "mono muted" }, detection.rule_id), detection.signals.length > 0 && el("div", {}, detection.signals.join(" · "))]))),
    item.relations.length > 0 && el("ul", { class: "record-list" }, item.relations.map(relation => el("li", {}, [el("strong", {}, labels[relation.kind]), el("div", {}, `${relation.source} → ${relation.target}`)]))),
    Object.keys(item.attributes).length > 0 && el("details", {}, [el("summary", {}, "提取的资料字段"), jsonView(item.attributes)]),
    !email && responses.map(artifact => {
      const content = el("div", { class: "artifact-block" });
      content.append(button(`查看原始响应 · ${artifact.artifact_id.slice(0, 10)}`, () => loadArtifact(run, artifact, content), "text-button"));
      return content;
    }),
  ])]);
  return el("article", { class: "panel evidence-card", id: `evidence-${item.evidence_id}` }, [
    el("div", { class: "evidence-heading" }, [el("small", { class: "muted" }, labels[item.kind]), status(item.state), item.attributes.conflict && el("span", { class: "status blocked" }, "规则结论冲突")]),
    el("h3", {}, item.attributes.site || item.attributes.site_id || item.subject),
    el("p", { class: "evidence-summary" }, item.summary),
    item.kind === "phone" && phoneDetails(item),
    email ? emailCheckEvidence(run, item, responses) : item.source_url && safeLink(item.source_url, item.source_url, { class: "source-link" }),
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
    pages: "页面按来源地址去重。同名仅表示候选关联。",
    email: "公开头像、公开资料和账户注册状态分别记录。",
    metadata: "IP 地理位置为估算值。",
    uncertain: "连接失败的原因见执行记录。",
  };
  panel.replaceChildren(el("div", { class: "results-toolbar", hidden: Boolean(focusId) }, [filters, search]), note, emailSummary, rows, pager);
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
    note.hidden = !focusId && (!notices[view] || (view === "metadata" && !kinds.has("ip")));
    note.replaceChildren(...(focusId ? [button("返回资料列表", () => { location.hash = `#/research/${detail.id}/evidence`; }, "text-button")] : [notices[view] || ""]));
    const failuresOpen = $("details", emailSummary)?.open;
    emailSummary.replaceChildren(...(view === "email" && !focusId && data.email_progress.planned > 0 ? [emailProgress(data.email_progress)] : []));
    if (failuresOpen && $("details", emailSummary)) $("details", emailSummary).open = true;
    const existing = new Map([...rows.querySelectorAll(".evidence-card")].map(node => [node.id, node]));
    rows.replaceChildren(...(data.items.length ? data.items.map(item =>
      existing.get(`evidence-${item.evidence_id}`) || evidenceCard(detail.id, item, data.artifacts)
    ) : [empty(query ? "没有匹配的资料" : "当前没有此类资料", query ? "调整搜索内容，或切换资料分类。" : "查询进度和失败原因见执行记录。")]));
    pager.replaceChildren(pagination(offset, 30, data.filtered_total, async value => { offset = value; await load(); rows.scrollIntoView({ block: "start" }); }));
    if (focusId) $(`#evidence-${CSS.escape(focusId)}`)?.scrollIntoView({ block: "start" });
  };
  search.addEventListener("input", debounce(async () => { query = search.value.trim(); offset = 0; focusId = null; await load(); }));
  await load();
  return load;
}

async function showReport(detail, panel, epoch) {
  const data = await api(`/research/${detail.id}/report/summary`);
  if (epoch !== state.route) return;
  const report = data.report;
  panel.replaceChildren(el("article", { class: "report-sheet" }, [
    el("div", { class: "panel-title" }, [el("h2", {}, "研究结论"), el("small", { class: "muted" }, `${data.status.evidence_count} 条证据`)]),
    (data.status.coverage.failed > 0 || data.status.coverage.blocked > 0) && el("p", { class: "coverage-summary" }, `初始查询中有 ${data.status.coverage.failed} 项连接失败，${data.status.coverage.blocked} 项受限。`),
    report?.claims.length ? report.claims.map(claim => el("section", { class: "claim" }, [el("p", {}, claim.text), el("div", {}, citations(detail.id, claim.evidence_ids)), claim.conflicting_evidence_ids.length > 0 && el("div", { class: "conflict" }, ["冲突证据：", ...citations(detail.id, claim.conflicting_evidence_ids)])])) : el("p", { class: "muted" }, terminal.has(detail.status) ? "本次研究尚无结论。" : "报告尚未生成。"),
    report?.next_actions.length > 0 && el("section", {}, [el("h3", {}, "后续行动"), el("ul", { class: "record-list" }, report.next_actions.map(item => el("li", {}, item)))]),
    el("div", { class: "toolbar section-gap" }, [el("a", { class: "button small", href: `/api/research/${detail.id}/report?format=markdown&download=true` }, "下载 Markdown"), el("a", { class: "button small", href: `/api/research/${detail.id}/report?format=json&download=true` }, "下载 JSON")]),
  ]));
}

async function showConfirmation(detail, panel, epoch) {
  if (!terminal.has(detail.status)) { panel.replaceChildren(empty("研究结束后可以确认关系", "确认声明将作为用户提供的证据加入报告。")); return; }
  const { urls } = await api(`/research/${detail.id}/confirm/sources`);
  if (epoch !== state.route) return;
  if (urls.length < 2) { panel.replaceChildren(empty("需要两条已有来源", "研究资料中至少出现两个公开地址后，可以记录你已经确认的关联。")); return; }
  const source = el("select", { required: true, "aria-label": "来源地址" }, urls.map(url => el("option", { value: url }, url)));
  const target = el("select", { required: true, "aria-label": "关联地址" }, urls.map(url => el("option", { value: url }, url)));
  target.selectedIndex = 1;
  const statement = el("textarea", { required: true, maxlength: 4000, rows: 4, "aria-label": "确认声明", placeholder: "写出你已经确认的关系及依据。" });
  const form = el("form", { class: "panel" }, [el("h2", {}, "记录关系确认"), el("p", { class: "muted" }, "你的声明将单独标注为用户确认，并补充到报告中。"), el("label", { class: "field" }, ["来源地址", source]), el("label", { class: "field" }, ["关联地址", target]), el("label", { class: "field" }, ["确认声明", statement]), el("button", { class: "button primary", type: "submit" }, "保存确认")]);
  form.addEventListener("submit", action(async event => { event.preventDefault(); await api(`/research/${detail.id}/confirm`, { method: "POST", body: JSON.stringify({ source: source.value, target: target.value, statement: statement.value }) }); location.hash = `#/research/${detail.id}/report`; }));
  panel.replaceChildren(form);
}

function budgetExtension(detail, refresh) {
  const seconds = el("input", { type: "number", min: 0, step: 1, max: Math.floor(86400 - detail.request.budget.run_timeout) });
  const requests = el("input", { type: "number", min: 0, step: 1, max: 1000000 - detail.request.budget.max_requests });
  const hasPage = detail.request.identifiers.some(item => item.kind === "profile");
  const apply = el("button", { type: "submit", class: "button primary" }, "继续原任务");
  const error = el("p", { class: "inline-error", role: "alert", hidden: true });
  const form = el("form", { class: "budget-extension" }, [
    el("label", { class: "field" }, ["追加运行时间（秒）", seconds]),
    hasPage && el("label", { class: "field" }, ["追加网络请求次数", requests]), apply, error,
  ]);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    apply.disabled = true;
    error.hidden = true;
    try {
      await api(`/research/${detail.id}/budget`, { method: "POST", body: JSON.stringify({
        additional_seconds: Number(seconds.value), additional_requests: hasPage ? Number(requests.value) : 0,
      }) });
      await refresh();
    } catch (failure) { error.textContent = failure.message; error.hidden = false; }
    finally { apply.disabled = false; }
  });
  return el("details", {}, [el("summary", {}, "追加预算并继续"), form]);
}

async function showResearch(id, tab, focusId, epoch) {
  const detail = await api(`/research/${id}?timeline=false`);
  if (epoch !== state.route) return;
  const main = $("#main"), summary = el("div", { class: "run-strip", "aria-live": "polite" }), controls = el("div", { class: "toolbar" });
  const panel = el("div"), runNotice = el("div", { class: "run-notice", hidden: true });
  let evidenceCount = -1, previousStatus = "", previousActions = "", previousRound, previousReport, refreshEvidence = null;
  main.replaceChildren(el("header", { class: "research-heading" }, [el("a", { class: "back-link", href: "#/tasks" }, "返回研究"),
    heading(detail.request.identifiers.length ? detail.request.identifiers[0].value + (detail.request.identifiers.length > 1 ? ` 等 ${detail.request.identifiers.length} 条线索` : "") : "公开资料研究", detail.request.goal, [controls])]), summary, runNotice,
    el("nav", { class: "tabs", "aria-label": "研究内容" }, [["evidence", "发现资料"], ["report", "研究报告"], ["overview", "执行记录"], ["confirm", "确认关系"]].map(([key, text]) => el("a", { href: `#/research/${id}/${key}`, class: tab === key ? "active" : "", "aria-current": tab === key ? "page" : null }, text))), panel);
  const update = async current => {
    if (epoch !== state.route) return;
    const done = current.coverage.checked + current.coverage.failed + current.coverage.blocked;
    summary.replaceChildren(status(current.status), el("span", { class: "run-progress" }, [el("span", {}, `${done.toLocaleString("zh-CN")} / ${current.coverage.planned.toLocaleString("zh-CN")} 项初始查询已处理`),
      current.coverage.planned > 0 && el("progress", { value: done, max: current.coverage.planned, "aria-label": "初始查询进度" })]), el("span", { class: "muted" }, duration(current.elapsed)),
      current.coverage.remaining > 0 && terminal.has(current.status) && el("span", { class: "muted" }, `尚有 ${current.coverage.remaining.toLocaleString("zh-CN")} 项待执行`));
    const reportNotice = current.report_status === "failed" ? `报告生成失败。 ${current.report_error || ""}` :
      current.report_status === "generating" ? "正在生成报告。" :
      current.report_status === "outdated" && tab === "report" ? "旧版报告，可重新核对证据。" : "";
    runNotice.hidden = !(current.stop_reason || reportNotice);
    if (!runNotice.hidden) {
      runNotice.replaceChildren(el("span", {}, [current.stop_reason, reportNotice].filter(Boolean).join("　")));
      if (current.error && tab !== "overview") runNotice.append(el("a", { href: `#/research/${id}/overview` }, "查看执行详情"));
    }
    if (current.status !== previousStatus || previousReport !== current.report_status) {
      controls.replaceChildren();
      if (!terminal.has(current.status)) controls.append(button("取消研究", async () => { await api(`/research/${id}/cancel`, { method: "POST", body: "{}" }); await refresh(); }, "button danger"));
      else if (current.can_extend_budget) controls.append(budgetExtension(current, async () => {
        Object.assign(detail, await api(`/research/${id}?timeline=false`));
        await update(detail);
      }));
      else if (current.can_resume) {
        const retry = el("input", { type: "checkbox", id: "retry-accounts" });
        if (current.uncertain_accounts) controls.append(el("label", { class: "retry-label" }, [retry, `允许重试 ${current.uncertain_accounts} 项未确认的账户请求`]));
        controls.append(button("恢复研究", async () => { await api(`/research/${id}/resume`, { method: "POST", body: JSON.stringify({ retry_interrupted_accounts: retry.checked }) }); await refresh(); }, "button primary"));
      }
      if (current.can_retry_report && tab === "report") controls.append(button("重新生成报告", async () => {
        await api(`/research/${id}/report/retry`, { method: "POST", body: "{}" });
        await refresh();
      }, "button primary"));
      controls.append(button("重新研究", () => showNew(current.request), "button small"));
    }
    if (tab === "overview" && (current.status !== previousStatus || current.latest_round !== previousRound || JSON.stringify(current.actions) !== previousActions)) {
      const execution = await api(`/research/${id}/execution`);
      if (epoch !== state.route) return;
      panel.replaceChildren(overview({ ...current, ...execution }));
    }
    else if (tab === "evidence" && (current.evidence_count !== evidenceCount || current.status !== previousStatus || JSON.stringify(current.actions) !== previousActions)) {
      if (refreshEvidence) await refreshEvidence();
      else refreshEvidence = await showEvidence(current, panel, epoch, focusId);
    }
    else if (tab === "report" && (current.report_status !== previousReport || current.status !== previousStatus || current.evidence_count !== evidenceCount)) await showReport(current, panel, epoch);
    else if (tab === "confirm" && current.status !== previousStatus) await showConfirmation(current, panel, epoch);
    if (epoch !== state.route) return;
    evidenceCount = current.evidence_count; previousStatus = current.status; previousActions = JSON.stringify(current.actions); previousRound = current.latest_round;
    previousReport = current.report_status;
    clearTimeout(state.poll);
    if (!terminal.has(current.status) || current.report_status === "generating") state.poll = setTimeout(() => refresh().catch(showError), 2000);
  };
  const refresh = async () => update({ ...detail, ...await api(`/research/${id}/progress`) });
  await update(detail);
}

function domesticPlatforms() {
  const states = { available: "用户名查询", restricted: "查询受限", profile_only: "使用主页链接" };
  return el("details", { class: "domestic-platforms" }, [
    el("summary", {}, "常用国内平台的输入要求"),
    el("div", { class: "platform-list" }, state.boot.domestic_platforms.map(platform => el("div", {}, [
      el("h3", {}, [safeLink(platform.url, platform.name), el("small", {}, states[platform.username_state])]),
      el("p", {}, platform.input_hint), platform.note && el("p", { class: "hint" }, platform.note),
    ]))),
  ]);
}

async function showCatalog(epoch) {
  const main = $("#main"), content = el("div");
  const tabs = el("div", { class: "tabs" });
  const render = async () => {
    tabs.replaceChildren(...[["username", "用户名站点"], ["email", "邮箱查询站点"]].map(([key, text]) => el("button", {
      type: "button", "aria-pressed": state.catalogTab === key ? "true" : "false",
      class: state.catalogTab === key ? "active" : "", onclick: action(async () => { state.catalogTab = key; await render(); }),
    }, text)));
    if (state.catalogTab === "email") {
      const providers = [...state.boot.capabilities.email.providers].sort((a, b) => Number(b.region === "CN") - Number(a.region === "CN"));
      const input = el("input", { type: "search", class: "search", placeholder: "站点名称或域名", "aria-label": "搜索邮箱站点", value: state.emailCatalogQuery });
      const region = el("select", { "aria-label": "邮箱站点地区" }, [el("option", { value: "" }, "全部站点"), el("option", { value: "CN" }, "国内及中文站点")]);
      region.value = state.emailCatalogRegion;
      const count = el("p", { class: "catalog-result-count", role: "status" }), rows = el("div", { class: "provider-grid" });
      content.replaceChildren(el("div", { class: "catalog-controls" }, [input, region]), count,
        el("p", { class: "hint" }, "实测记录来自自有样本，准确率待核实。"), rows);
      const filter = () => {
        const terms = state.emailCatalogQuery.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
        const visible = providers.filter(provider => (!state.emailCatalogRegion || provider.region === state.emailCatalogRegion) && terms.every(term => `${provider.name} ${provider.site} ${provider.source}`.toLocaleLowerCase().includes(term)));
        count.textContent = visible.length === providers.length ? `共 ${providers.length} 个邮箱站点` : `${providers.length} 个邮箱站点中有 ${visible.length} 个符合条件`;
        rows.replaceChildren(...(visible.length ? visible.map(provider => el("article", { class: "panel provider-card", "data-site": provider.site }, [
          el("div", { class: "provider-heading" }, [el("h3", {}, [provider.name, provider.region === "CN" && el("span", { class: "tag" }, "中国")]), status(provider.last_check.state)]),
          el("div", { class: "provider-body" }, [el("span", { class: "tag" }, labels[provider.interaction]), el("p", {}, provider.meaning), safeLink(provider.source, "官方来源")]),
          el("div", { class: "provider-check" }, [el("span", {}, provider.last_check.observation), el("small", {}, `${provider.last_check.checked_on} · ${provider.last_check.samples} 个自有样本`)]),
        ])) : [empty("未找到对应站点", "可以修改站点名称或切换全部站点浏览。") ]));
      };
      input.addEventListener("input", () => { state.emailCatalogQuery = input.value; filter(); });
      region.addEventListener("change", () => { state.emailCatalogRegion = region.value; filter(); });
      filter();
      return;
    }
    const input = el("input", { type: "search", class: "search", placeholder: "中文名称、英文名称或域名", "aria-label": "搜索站点名称", value: state.catalogQuery });
    const region = el("select", { "aria-label": "站点地区" }, [el("option", { value: "CN" }, "国内站点"), el("option", { value: "" }, "全部地区")]);
    region.value = state.catalogRegion;
    const results = el("div", { class: "section-gap", tabindex: "-1" });
    content.replaceChildren(el("div", { class: "catalog-controls" }, [input, region]), domesticPlatforms(), results);
    const load = async () => {
      const query = state.catalogQuery, offset = state.catalogOffset, selectedRegion = state.catalogRegion;
      const data = await api(`/capabilities?${new URLSearchParams({ offset, limit: 30, query, region: selectedRegion })}`);
      if (epoch !== state.route || state.catalogTab !== "username" || query !== state.catalogQuery || offset !== state.catalogOffset || selectedRegion !== state.catalogRegion) return;
      const range = data.sites.length ? ` · 显示 ${offset + 1}–${offset + data.sites.length} 项` : "";
      results.replaceChildren(el("p", { class: "catalog-result-count", role: "status" }, `共 ${data.total_sites.toLocaleString("zh-CN")} 项，已适配 ${data.supported_sites.toLocaleString("zh-CN")} 项${range}`),
      data.sites.length ? el("div", { class: "provider-grid" }, data.sites.map(site => el("article", { class: "panel provider-card" }, [
        el("div", { class: "provider-heading" }, [el("h3", {}, site.name), el("span", { class: "tag" }, site.supported ? "规则已适配" : "规则受限")]),
        el("div", { class: "provider-body" }, [site.aliases.length > 0 && el("p", {}, site.aliases.join(" · ")), el("p", { class: "mono" }, site.profile_template), el("p", { class: "site-input-hint" }, site.input_hint), el("small", {}, `规则来源：${site.rules.map(rule => rule.rule_id.split(":")[0]).join(" + ")}`), ...site.rules.filter(rule => rule.unsupported).map(rule => el("p", {}, rule.unsupported))]),
      ]))) : empty("未找到对应站点", "请调整搜索内容或地区。"));
      if (data.total_sites > 30) results.append(pagination(offset, 30, data.total_sites, async value => {
        state.catalogOffset = value;
        await load();
        if (epoch === state.route && state.catalogTab === "username") results.focus();
      }));
    };
    input.addEventListener("input", debounce(async () => { state.catalogQuery = input.value; state.catalogOffset = 0; await load(); }));
    region.addEventListener("change", action(async () => { state.catalogRegion = region.value; state.catalogOffset = 0; await load(); }));
    await load();
  };
  main.replaceChildren(heading("站点目录", "目录筛选仅影响当前列表。"), tabs, content);
  await render();
}

async function showCompare(epoch) {
  const runs = [];
  let offset = 0, total;
  do { const page = await api(`/research?limit=100&offset=${offset}`); runs.push(...page.items); total = page.total; offset += 100; } while (offset < total);
  if (epoch !== state.route) return;
  const main = $("#main"), results = el("div");
  if (runs.length < 2) {
    main.replaceChildren(heading("结果比较"), empty("需要两项研究才能比较", `当前有 ${runs.length} 项研究。`, [button("新建研究", () => showNew(), "button primary")]));
    return;
  }
  const options = () => runs.map(run => el("option", { value: run.id }, `${date(run.created_at)} · ${run.request.goal}`));
  const previous = el("select", { "aria-label": "较早的研究" }, options()), current = el("select", { "aria-label": "较新的研究" }, options());
  previous.selectedIndex = 1;
  const submit = el("button", { class: "button primary", type: "submit" }, "比较结果");
  const form = el("form", { class: "panel compare-form" }, [el("label", { class: "field" }, ["较早的研究", previous]), el("label", { class: "field" }, ["较新的研究", current]), submit]);
  form.addEventListener("change", () => {
    submit.disabled = previous.value === current.value;
    results.replaceChildren();
  });
  form.addEventListener("submit", action(async event => {
    event.preventDefault();
    const before = previous.value, after = current.value;
    if (before === after) return;
    const data = await api(`/compare/${before}/${after}`);
    if (epoch !== state.route || before !== previous.value || after !== current.value) return;
    const groups = Object.entries(data.changes).filter(([, items]) => items.length).map(([kind, items]) => {
      const rows = items.map(item => {
        const pair = [item.previous, item.current].map((record, index) => el("div", {}, [
          el("small", {}, index ? "较新研究" : "较早研究"),
          record ? [el("p", {}, record.summary), status(record.state),
            el("div", {}, citations(index ? after : before, [record.evidence_id]))]
            : el("p", {}, index && item.failure ? `未取得结果：${item.failure}` : "没有对应记录"),
        ]));
        return el("article", { class: "comparison-row" }, [
          el("strong", {}, item.current?.subject || item.previous.subject),
          el("div", { class: "comparison-pair" }, pair),
        ]);
      });
      return el("section", { class: "panel comparison-group" }, [
        el("div", { class: "panel-title" }, [el("h3", {}, labels[kind]), el("span", { class: "mono" }, `${items.length} 项`)]),
        rows,
      ]);
    });
    results.replaceChildren(...(groups.length ? groups : [empty("两项研究均无可比较的记录")]));
  }));
  main.replaceChildren(heading("结果比较"), form, results);
}

function showSettings() {
  const boot = state.boot;
  const result = el("span", { role: "status" }, boot.model_configured ? "已配置" : "缺少配置");
  const check = button("检查模型连接", async () => {
    check.textContent = "正在检查连接…";
    result.textContent = "已配置";
    try {
      await api("/doctor", { method: "POST", body: "{}" });
      result.textContent = "模型连接检查通过。";
    } finally {
      check.textContent = "检查模型连接";
    }
  });
  $("#main").replaceChildren(heading("运行设置"), el("div", { class: "settings-grid" }, [
    el("section", { class: "panel" }, [el("h2", {}, "模型连接"), info([["状态", result], ["模型", boot.model || "尚未填写"], ["服务地址", boot.base_url || "尚未填写"], ["密钥变量", boot.api_key_env]]), el("p", { class: "hint section-gap" }, "修改环境变量或配置文件后，重新启动工作台。"), boot.model_configured && check]),
    el("section", { class: "panel" }, [el("h2", {}, "工作空间"), info([["应用版本", boot.version], ["研究资料", el("span", { class: "mono" }, boot.data_dir)]]), el("p", { class: "hint section-gap" }, "关闭网页后任务继续运行。服务重启后可恢复中断的任务。")]),
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

function siteScopePicker(label, initialSites, saved, searchSites, edited) {
  const chosen = new Map(saved?.entries ?? initialSites);
  const limited = el("input", { type: "checkbox", checked: saved?.limited ?? chosen.size > 0 });
  const search = el("input", { type: "search", maxlength: 200, autocomplete: "off", placeholder: "输入站点名称", "aria-label": `搜索${label}站点`, value: saved?.query || "" });
  const selected = el("div", { class: "scope-choices" }), matches = el("div", { class: "scope-choices" });
  const input = el("div", { class: "scope-input", hidden: !limited.checked }, [selected, search, matches]);
  const row = el("div", { hidden: true }, [el("label", { class: "scope-toggle" }, [limited, `仅查询指定${label}站点`]), input]);
  let sequence = 0;
  const labelled = entries => {
    const counts = new Map();
    for (const [, name] of entries) counts.set(name, (counts.get(name) || 0) + 1);
    return entries.map(([id, name]) => ({ id, name, text: counts.get(name) > 1 ? `${name} · ${id}` : name }));
  };
  const renderSelected = () => {
    selected.replaceChildren(...labelled([...chosen]).map(({ id, text }) => el("button", {
      type: "button", class: "button small", "aria-label": `移除 ${text}`, onclick: () => {
        chosen.delete(id); renderSelected(); search.focus(); edited();
      },
    }, `${text} ×`)));
  };
  const lookup = debounce(async () => {
    const current = sequence, query = search.value.trim();
    if (!query || !limited.checked || !row.isConnected) return;
    try {
      const choices = await searchSites(query);
      if (current !== sequence || !row.isConnected) return;
      matches.replaceChildren(...labelled(choices).map(({ id, name, text }) => el("button", {
        type: "button", class: "button small", onclick: () => {
          chosen.set(id, name);
          search.value = ""; sequence++; matches.replaceChildren(); renderSelected(); search.focus(); edited();
        },
      }, text)));
      if (!choices.length) matches.textContent = "没有匹配的站点。";
    } catch (failure) {
      if (current === sequence && row.isConnected) matches.textContent = failure.message;
    }
  });
  search.addEventListener("input", () => { sequence++; matches.replaceChildren(); lookup(); edited(); });
  search.addEventListener("keydown", event => { if (event.key === "Enter") event.preventDefault(); });
  limited.addEventListener("change", () => {
    sequence++; input.hidden = !limited.checked; matches.replaceChildren();
    if (limited.checked) { search.focus(); lookup(); }
    edited();
  });
  renderSelected();
  if (search.value) lookup();
  return {
    row,
    refresh: () => { sequence++; matches.replaceChildren(); if (search.value.trim()) lookup(); },
    resolve: choices => {
      if (choices.length === chosen.size && choices.every(({ id, name }) => chosen.get(id) === name)) return;
      chosen.clear(); choices.forEach(({ id, name }) => chosen.set(id, name)); renderSelected();
    },
    sites: () => limited.checked ? [...chosen.keys()] : undefined,
    draft: () => ({ limited: limited.checked, entries: [...chosen], query: search.value }),
    names: () => labelled([...chosen]).map(site => site.text),
    incomplete: () => !row.hidden && limited.checked && (!chosen.size || Boolean(search.value.trim())),
  };
}

function goalScopeNames(names) {
  const platforms = [...new Set(names)].join("、");
  return names.length > 1 ? `${platforms}（${names.length} 个站点）` : platforms;
}

function researchComposer(epoch) {
  const draft = state.draft || { clues: "", goal: "", budget: state.boot.budget };
  const clues = el("textarea", { id: "clues", rows: 3, required: true, maxlength: 42000, autocomplete: "off", spellcheck: "false", placeholder: "用户名、主页链接、邮箱、电话号码或公共 IP", "aria-describedby": "clue-help" }, draft.clues);
  const goal = el("textarea", { id: "goal", rows: 2, maxlength: 8000, placeholder: "例如，项目主页是否链接到官方网站？" }, draft.goal);
  const preview = el("div", { class: "intake-preview", "aria-live": "polite" });
  const error = el("div", { id: "form-error", class: "inline-error", role: "alert", hidden: true });
  const submit = el("button", { id: "submit-research", type: "submit", class: "button primary", disabled: true }, "开始研究");
  const modelNote = el("p", { class: "hint", id: "model-use", role: "status", hidden: true });
  let requiresModel = null, submitting = false, scopeReady = false;
  function updateModelAccess(required) {
    requiresModel = required;
    submit.disabled = submitting || !scopeReady || (required !== false && !state.boot.model_configured);
    modelNote.hidden = required === null;
    if (required === null) return;
    if (!required) modelNote.textContent = "号段资料和报告在本机生成。";
    else if (state.boot.model_configured) modelNote.textContent = `研究问题、线索和证据摘录将发送至模型服务 ${new URL(state.boot.base_url).host}。`;
    else modelNote.replaceChildren("此类研究需要模型服务。", el("a", { href: "#/settings" }, "查看运行设置"));
  }
  const fields = el("div", { id: "budget-fields", class: "budget-grid" }, Object.entries(budgetLabels).map(([key, [label, min, max]]) => el("label", { class: "field" }, [label, el("input", { type: "number", name: key, value: draft.budget[key] ?? state.boot.budget[key], min, max, step: key.endsWith("timeout") ? "any" : 1, required: true })])));
  const depth = $('[name="max_depth"]', fields);
  const follow = el("input", { type: "checkbox", checked: draft.followProfileLinks === true });
  const followOption = el("label", { class: "scope-toggle", hidden: true }, [follow, "继续核查页面外链"]);
  let legacyProfileSearch = draft.legacyProfileSearch === true;
  follow.addEventListener("change", () => {
    legacyProfileSearch = false;
    if (follow.checked && !Number(depth.value)) depth.value = "1";
    scopeEdited();
  });
  const scope = el("p", { id: "default-scope", class: "hint", role: "status", hidden: true });
  const email = state.boot.capabilities.email;
  const categories = state.boot.capabilities.site_categories;
  const chosenCategories = new Set(draft.site_categories || []);
  let allCategories = draft.allCategories === true;
  const previousEmail = draft.email_sites || [];
  const allEmail = previousEmail.length === email.sites.length && previousEmail.every((site, index) => site === email.sites[index]);
  const emailNames = new Map(email.providers.map(provider => [provider.site, provider.name]));
  const usernamePicker = siteScopePicker("用户名", (draft.sites || []).map(site => [site, site]), draft.scopeEditors?.username, async query => {
    const params = new URLSearchParams({ query, limit: 30, supported_only: true });
    for (const key of chosenCategories) params.append("categories", key);
    const data = await api(`/capabilities?${params}`);
    return data.sites.map(site => [site.site_id, site.name]);
  }, scopeEdited);
  const emailPicker = siteScopePicker("邮箱", (allEmail ? [] : previousEmail).map(site => [site, emailNames.get(site)]), draft.scopeEditors?.email, query => {
    const term = query.toLocaleLowerCase();
    return email.providers.filter(provider => (!chosenCategories.size || chosenCategories.has(provider.category)) &&
      `${provider.name} ${provider.site} ${provider.source}`.toLocaleLowerCase().includes(term))
      .slice(0, 30).map(provider => [provider.site, provider.name]);
  }, scopeEdited);
  const scopeOptions = el("details", { class: "scope-options", open: Boolean(usernamePicker.sites() || emailPicker.sites()) }, [el("summary", {}, "按名称选择站点"), usernamePicker.row, emailPicker.row]);
  const categoryOptions = categories.map(({ id, label, username_count, email_count }) => {
    const input = el("input", { type: "checkbox", value: id, checked: chosenCategories.has(id) });
    const option = el("label", { class: "category-option" }, [input, label]);
    input.addEventListener("change", () => {
      allCategories = false;
      allInput.checked = false;
      if (input.checked) chosenCategories.add(id);
      else chosenCategories.delete(id);
      categoriesEdited();
    });
    return { input, option, username_count, email_count };
  });
  const allInput = el("input", { type: "checkbox", checked: allCategories });
  const allLabel = el("span", {}, "全部站点");
  allInput.addEventListener("change", () => {
    allCategories = allInput.checked;
    chosenCategories.clear();
    categoryOptions.forEach(({ input }) => { input.checked = false; });
    categoriesEdited();
  });
  const categoryGroup = el("fieldset", { class: "scope-categories", hidden: true }, [
    el("legend", {}, "站点类别"), el("div", { class: "category-options" }, [
      ...categoryOptions.map(({ option }) => option), el("label", { class: "category-option" }, [allInput, allLabel]),
    ]), scopeOptions,
  ]);
  const budgetOptions = el("details", { class: "research-options" }, [el("summary", {}, "请求与时间预算"), fields]);
  budgetOptions.addEventListener("invalid", () => { budgetOptions.open = true; }, true);
  const form = el("form", { id: "research-form", class: "composer" }, [
    el("div", { class: "composer-layout" }, [
      el("div", { class: "field" }, [el("label", { for: "clues" }, "已知线索"), clues,
        el("p", { id: "clue-help", class: "hint" }, "填写自己或获授权对象的线索，每行一条，最多 20 条。国际号码请附国家代码，数字用户名请加 username: 前缀。"), preview]),
      el("div", { class: "field" }, [el("label", { for: "goal" }, "研究问题（选填）"), goal]),
    ]),
    followOption, categoryGroup, budgetOptions, error,
    el("div", { class: "composer-footer" }, [el("div", {}, [scope, modelNote]), submit]),
  ]);
  let generation = 0, previewTimer;
  const read = () => ({ follow_profile_links: legacyProfileSearch && follow.checked ? null : follow.checked,
    site_categories: chosenCategories.size ? [...chosenCategories] :
      (follow.checked && !legacyProfileSearch && allCategories ? categories.map(item => item.id) : null),
    sites: usernamePicker.sites(), email_sites: emailPicker.sites(), clues: clues.value, goal: goal.value.trim(), budget: {
    ...draft.budget, ...Object.fromEntries(Object.keys(budgetLabels).map(key => [key, Number($(`[name="${key}"]`, fields).value)])),
  } });
  const saveDraft = () => { state.draft = { ...read(), site_categories: [...chosenCategories], allCategories, followProfileLinks: follow.checked, legacyProfileSearch,
    scopeEditors: { username: usernamePicker.draft(), email: emailPicker.draft() } }; };
  function categoriesEdited() {
    usernamePicker.refresh(); emailPicker.refresh(); scopeEdited();
  }
  function scopeEdited() {
    saveDraft();
    scopeReady = false;
    updateModelAccess(requiresModel);
    scope.hidden = true; generation++; clearTimeout(previewTimer);
    previewTimer = setTimeout(() => prepare(), 200);
  }
  const prepare = async (values = read()) => {
    if (epoch !== state.route) return null;
    const sequence = ++generation;
    error.hidden = true;
    if (!clues.value.trim()) { preview.replaceChildren(); return null; }
    try {
      const data = await api("/prepare", { method: "POST", body: JSON.stringify(values) });
      if (epoch !== state.route || sequence !== generation) return null;
      const request = data.request;
      preview.replaceChildren(...data.identifiers.map(item => el("span", { class: "clue-chip" }, [el("strong", {}, labels[item.kind]), el("span", {}, item.value)])));
      const kinds = new Set(data.identifiers.map(item => item.kind));
      if (data.username_sites) usernamePicker.resolve(data.username_sites);
      const onlyProfiles = kinds.size === 1 && kinds.has("profile");
      followOption.hidden = !onlyProfiles;
      depth.closest("label").hidden = onlyProfiles && !follow.checked;
      depth.min = onlyProfiles && follow.checked ? "1" : "0";
      const hasProfileScope = kinds.has("profile") && request.budget.max_depth > 0;
      usernamePicker.row.hidden = !kinds.has("username") && !hasProfileScope;
      emailPicker.row.hidden = !kinds.has("email");
      categoryGroup.hidden = usernamePicker.row.hidden && emailPicker.row.hidden;
      allLabel.textContent = ((!usernamePicker.row.hidden && usernamePicker.sites()) || (!emailPicker.row.hidden && emailPicker.sites())) ? "全部类别" : "全部站点";
      categoryOptions.forEach(({ input, option, username_count, email_count }) => {
        option.hidden = !input.checked && !((!usernamePicker.row.hidden && username_count) || (!emailPicker.row.hidden && email_count));
      });
      const incompleteSites = usernamePicker.incomplete() || emailPicker.incomplete();
      const specified = (usernamePicker.row.hidden || usernamePicker.sites()?.length) && (emailPicker.row.hidden || emailPicker.sites()?.length);
      scopeReady = !incompleteSites && (categoryGroup.hidden || allCategories || chosenCategories.size > 0 || specified || data.goal_sites.length > 0);
      updateModelAccess(data.requires_model);
      if (!scopeReady) {
        scope.hidden = false;
        if (incompleteSites) scopeOptions.open = true;
        scope.textContent = incompleteSites ? "请选择搜索结果，或取消指定站点。" : "请选择站点类别，或选择全部站点。";
        return null;
      }
      const parts = [];
      if (kinds.has("username")) {
        const { sites } = request;
        if (data.goal_scope?.username.length) {
          parts.push(`用户名检查 ${goalScopeNames(data.goal_scope.username)}`);
        } else if (usernamePicker.sites()?.length && data.scope.username_sites === sites.length) {
          const names = usernamePicker.names().slice(0, 3).join("、");
          parts.push(sites.length > 3 ? `用户名检查 ${names} 等 ${sites.length} 个指定站点` : `用户名检查 ${names}`);
        } else {
          parts.push(`用户名检查 ${data.scope.username_sites.toLocaleString("zh-CN")} 个站点`);
        }
      }
      if (kinds.has("email")) {
        const names = emailPicker.names();
        if (data.goal_scope?.email.length) parts.push(`邮箱检查 ${goalScopeNames(data.goal_scope.email)}`);
        else parts.push(emailPicker.sites() && names.length === data.scope.email_sites && names.length <= 3 ? `邮箱检查 ${names.join("、")}` : `邮箱检查 ${data.scope.email_sites} 个站点`);
      }
      if (kinds.has("profile")) {
        parts.push("读取所填主页");
        if (hasProfileScope) {
          const purpose = request.follow_profile_links ? "核查页面外链" : "核查页面外链和关联用户名";
          parts.push(`${purpose}最多 ${request.budget.max_depth} 层，限 ${data.scope.username_sites.toLocaleString("zh-CN")} 个站点`);
        }
      }
      if (kinds.has("phone")) parts.push("查询号码号段资料");
      if (kinds.has("ip")) parts.push("查询 IP 网络归属");
      scope.textContent = `初始范围：${parts.join("，")}`;
      scope.hidden = false;
      return request;
    } catch (failure) {
      if (epoch === state.route && sequence === generation) {
        scopeReady = false;
        categoryGroup.hidden = !failure.scopeField;
        if (failure.scopeField === "sites") {
          usernamePicker.row.hidden = false; scopeOptions.open = true;
        }
        updateModelAccess(null);
        scope.hidden = true; preview.replaceChildren(); error.textContent = failure.message; error.hidden = false;
      }
      return null;
    }
  };
  clues.addEventListener("input", () => {
    if (!clues.value.trim()) { followOption.hidden = true; categoryGroup.hidden = true; }
    scopeReady = false;
    updateModelAccess(null);
    generation++; scope.hidden = true; preview.replaceChildren(); clearTimeout(previewTimer);
    previewTimer = setTimeout(() => prepare(), 350);
  });
  fields.addEventListener("input", scopeEdited);
  goal.addEventListener("input", scopeEdited);
  form.addEventListener("input", saveDraft);
  form.addEventListener("submit", async event => {
    event.preventDefault(); clearTimeout(previewTimer);
    const values = read();
    submitting = true;
    const controls = [...form.querySelectorAll("input, textarea, button")].map(node => [node, node.disabled]);
    controls.forEach(([node]) => { node.disabled = true; });
    submit.textContent = "正在创建研究…";
    try {
      const request = await prepare(values);
      if (!request || (requiresModel && !state.boot.model_configured)) return;
      const data = await api("/research", { method: "POST", body: JSON.stringify(request) });
      state.draft = null; location.hash = `#/research/${data.run_id}/evidence`;
    } catch (failure) { error.textContent = failure.message; error.hidden = false; }
    finally {
      controls.forEach(([node, disabled]) => { node.disabled = disabled; });
      submitting = false; updateModelAccess(requiresModel); submit.textContent = "开始研究";
    }
  });
  if (draft.clues) prepare();
  return form;
}

async function showNew(request = null) {
  const profileExpansion = request?.identifiers.length > 0 && request.identifiers.every(item => item.kind === "profile")
    && request.budget.max_depth > 0;
  const allLinkCategories = profileExpansion && request.follow_profile_links &&
    state.boot.capabilities.site_categories.every(({ id }) => request.site_categories?.includes(id));
  state.draft = request ? {
    clues: request.identifiers.map(item => item.kind === "username" ? `username:${item.value}` : item.value).join("\n"),
    goal: request.goal, budget: request.budget, sites: request.sites, email_sites: request.email_sites,
    site_categories: allLinkCategories ? [] : request.site_categories,
    followProfileLinks: profileExpansion, legacyProfileSearch: profileExpansion && request.follow_profile_links == null,
    allCategories: allLinkCategories || (!request.site_categories?.length && !request.goal_sites?.length && !request.goal_site && (profileExpansion || request.identifiers.some(item => ["username", "email"].includes(item.kind)))),
  } : null;
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
