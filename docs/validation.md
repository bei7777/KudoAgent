# 验证记录

日期：2026-11-11。环境：macOS、Python 3.12.11。依赖依据 `uv.lock`，包括 LangGraph 1.2.20、MCP Python SDK 2.3.0、HTTPX 0.28.1、Pydantic 2.14.3、defusedxml 0.7.1。

## 已执行检查

| 检查 | 实际结果 |
| --- | --- |
| Python 静态检查 | `ruff check src tests scripts` 通过 |
| 本地测试 | 166 项通过；使用真实规则文件、phonenumbers 数据、本地 SQLite、官方 MCP 客户端、真实 Starlette 应用和已有采集记录；包含 73 个社区的真实响应重新解释、100 项目录完整性、国内优先排序、邮箱动作进度、平台协议、号码资料及证据引用 |
| 输入与资料分类 | 本地准备接口自动识别线索，研究问题可以留空；大陆手机号支持 11 位、86、+86、空格与横线，统一规范化并去重，数字用户名可显式指定。预览和无效提交不创建任务。真实记录验证候选 URL 去重计数、多条证据保留、规则冲突、邮箱未知状态与号码资料分类 |
| 默认查询范围 | 两个用户名分别使用全部 8,303 个适配站点，共持久化 16,606 项初始查询；前 262 项覆盖两个用户名的全部国内站点。首批选择 50 项，模型规划调用数为 0，重新打开 SQLite 后完整队列与批次决定保持一致 |
| 国内站点目录 | 上游 `cn` 标签对应 137 项，其中 131 项已适配并优先执行，完整范围仍为 8,303 项；前两页目录均为国内站点，显式站点顺序保留；中文搜索和数字 ID、UID、会话限制说明通过真实目录测试 |
| 国内站点实际查询 | Gitee 的 `oschina` 用户名与公开主页均返回 `detected`，主页标题为开源中国，正文 1,910 字符；Maigret 与 Sherlock 规则共享响应。微博公开页面返回的访客验证页面记录为 `blocked`；真实存档复核时没有新增网络请求 |
| 公开网络测试 | 2 项通过；公开 GitHub 项目 API、Sherlock 项目账号与主页、公共 IP `1.1.1.1` |
| 请求共享与恢复读取 | 同一 GitHub API 请求并发调用仅发送 1 次；关闭并重新打开存储后复用资料；不同参数产生独立请求 |
| URL 查询参数 | 真实 httpbingo 请求保留 URL 内嵌参数，并正确合并显式请求参数 |
| 连接失败隔离 | 使用获授权的真实用户名执行前 100 项目录查询，91 项返回结果、9 项连接失败；失败记录包含请求地址和底层异常，第二批继续执行；恢复读取保留已处理查询，没有重复发送其请求 |
| 查询限制隔离 | 真实地址解析检查拒绝回环地址，号码资料查询继续完成，网络请求数为 0；恢复保留拒绝记录。实际站点查询中，HubPages 的非公共地址解析被单独标记为受限，其余站点继续执行 |
| 服务端 Cookie 限制 | Psychologies 的实际响应包含请求头无法使用的 Cookie 字符，查询明确记录为受限；同一 HTTP 客户端随后访问 Gitee 返回 HTTP 200 |
| 页面检测 | GitHub 账号与主页返回 `detected`，分别保留两套规则、页面资料、外链与标识符提及 |
| 号码资料与搜索建议 | phonenumbers 的 CN 手机和 CH 固话官方示例通过格式、号段、类型与时区检查；中国号码优先生成百度搜索链接，搜索建议均保留 `not_executed`，本地处理的网络请求数为 0 |
| 页面号码提及 | phonenumbers 官方文本示例、中国号码格式及 RFC 3966 的 `tel:` 示例通过准确匹配检查；真实访问官方号码库文档的 1 项在线测试通过。结果保存正文或链接中的位置、上下文、规范化号码与来源 |
| 邮箱查询进度 | 已有真实任务的 13 项邮箱查询准确显示为 12 项返回、1 项 Spotify 连接失败；失败站点名称、原始原因及其他失败动作计数均通过存档检查，搜索和分页保持全任务进度 |
| IP 资料 | ipwho.is 返回 `metadata`，保存完整 JSON、坐标及来源 |
| 来源完整性 | 原始资料校验、证据引用验证、重复动作识别和请求预算检查通过 |
| 结果比较 | 两次实际采集正确区分新增 IP 资料、受限页面、没有再次查询的主页与保持一致的号码资料；没有把受限页面记为消失 |
| MCP 通信 | 官方客户端完成初始化、9 个工具发现、能力查询、结构化结果读取和配置缺失错误检查 |
| Web 页面与访问控制 | 实际应用返回首页、脚本、样式与图标；Host、Origin、页面会话令牌、输入校验、路径限制和服务重启后令牌失效检查通过 |
| Web 真实浏览器流程 | Chrome 通过真实 DeepSeek 服务完成两次公开 Sherlock 项目主页研究；多行输入、选填问题、草稿保留、中文搜索、国内筛选、分页、默认全部范围、覆盖进度、键盘操作、资料分类与搜索、原始资料 SHA-256 校验、报告下载、引用定位和返回、取消恢复、结果比较以及 320、390、760、1024、1440 像素宽度布局检查通过；浏览器脚本错误为 0 |
| 国内目录、号码与邮箱页面 | 首页、国内目录、号码资料页和邮箱进度页分别在 320、390、760、1024、1440 像素宽度通过 DOM 布局边界检查；号码格式及搜索建议、真实邮箱任务的返回与失败进度显示通过，浏览器脚本错误为 0 |
| 邮箱目录 | Web、CLI 与 MCP 共用 100 个查询目标，包含 40 个国内及中文站点；默认全部查询，国内及中文站点优先。目录提供查询含义、验证日期与最近实测状态；名称搜索、地区浏览、空结果及 320、390、760、1440 像素宽度的真实浏览器 DOM 检查通过，浏览器错误为 0 |
| 邮箱接口观察 | 两个自有邮箱分别在 100 个目标执行过检查。73 个社区的最近 146 项记录为 29 项字段检查未返回占用、106 项未知、9 项访问阻断、2 项连接失败。未知结果包括 MyBB 字段可填写及 Discourse 成功响应，均保留各自含义；全部资料和引用通过验证，账户准确率尚未建立 |
| 邮箱协议验证 | 新增 12 个提供方的真实存档检查全部通过，核对原始 JSON、证据状态、引用及内容校验值；Microsoft 新解析函数通过真实成功响应验证，未新增网络请求。SoundCloud 按当前官方页面取得请求头配置，完整采集 HTTP 序列为 200 / 200 / 200 / 403 |
| 模型连接 | DeepSeek `deepseek-flash` 的真实结构化工具调用检查通过 |
| 自主研究 | 真实 DeepSeek 服务完成公开 GitHub 项目研究，生成报告并通过证据引用校验 |
| MCP 研究、取消与恢复 | 官方客户端通过真实 DeepSeek 服务创建研究，取得证据后取消任务；重新启动 MCP 服务并恢复任务，既有证据完整保留，报告生成成功 |
| 进程中断恢复 | 采集期间、模型报告生成期间和模型规划期间分别强制终止测试进程，再启动新进程恢复；3 项测试通过，已保存证据与响应保持完整 |
| 预算与模型记录 | 中断的规划调用计入次数预算；恢复后保留已保存耗时；原始模型请求、返回和服务端 token 用量持久化 |
| 构建 | wheel 与源码分发包构建成功，包含规则、来源记录和许可证 |
| 独立安装 | wheel 安装至干净 Python 环境后，包从 `site-packages` 加载；规则、来源记录、完整许可、CLI、官方 MCP 客户端读取 9 个工具与 Web 页面资源检查均通过 |
| 跨系统构建与安装 | 提交 `b9644ff` 的 GitHub Actions 在 macOS、Linux、Windows 均通过静态检查、测试、构建和独立安装验证；每个平台 71 项测试通过，21 项依赖私有采集记录的测试跳过，对应存档测试已经在本机通过 |

本地运行记录保存在 Git 忽略目录 `.runtime/`。`email100-all-tests.xml` 保存 166 项本地测试结果，`email-catalog-result.json` 保存 100 项邮箱目录的真实浏览器检查，`email100-checks/` 保存社区的真实字段检查；`china-final-browser/results.json` 保存真实浏览器流程的两项研究标识与检查结果，`china-ui-result.json` 和 `email-ui-result.json` 保存国内目录、号码与邮箱页面的布局检查；`all-scope-live.xml` 保存完整范围持久化、真实模型研究、MCP 取消恢复与三种进程中断恢复的 6 项通过记录；`domestic-profile-checks/` 保存国内公开主页查询资料；`domestic-verification/` 保存 Gitee 查询记录与模型报告；`comparison-results.xml` 保存两次实际采集的比较结果。公开网络测试保留默认 20 秒请求期限，执行结果受当前网络连接与站点响应影响。

三种操作系统的构建与安装结果见 [GitHub Actions 验证记录](https://github.com/bei7777/KudoAgent/actions/runs/42753081714)。100 项邮箱目录的独立安装环境为 `.runtime/email100-install/`，验证命令为 `scripts/verify_install.py .runtime/email100-install-check`，检查包内规则与许可资源、100 项邮箱目录、Web 页面以及 MCP 9 个工具。

完整目录测试验证了规则加载、初始范围和持久化。实际网络验证覆盖本页记录中的具体站点；8,303 项规则尚未逐项完成在线有效性测量。

连接失败检查记录保存在 `.runtime/network-isolation-result.json` 与 `.runtime/network-isolation/`，本地检查结果保存在 `.runtime/network-fix-offline.xml`。`.runtime/target-rejection.xml` 保存地址拒绝、服务端 Cookie 限制与独立查询的 2 项通过记录，`.runtime/network-recovery.xml` 保存 4 项真实 MCP 与进程中断恢复通过记录。运行资料包含用户的查询输入，保持 Git 忽略。

两个用户明确授权的自有邮箱均完成了 100 个目标的检查尝试。逐站结果见[邮箱站点](email-providers.md)与[社区邮箱检查](email-communities.md)，其中公开头像和资料查询保留各自含义。记录保存在 `.runtime/email-checks/`、`.runtime/email-checks-current/`、`.runtime/email-expanded/`、`.runtime/domestic-email-checks/`、`.runtime/email-expansion-checks/`、`.runtime/expansion-social/`、`.runtime/expansion-products/captured/` 和 `.runtime/email100-checks/`；各次执行的 `email-check.json`、SQLite 与原始响应可相互核验。每个社区均有两个不同自有邮箱的观察记录，全部通过 `Store.verify()`；连接失败单独保留。具体邮箱仅保存在私有样本和运行资料中。

## 仍需完成的验收

| 验收项 | 所需条件与当前状态 |
| --- | --- |
| 邮箱准确性与受限接口 | 已执行两个自有邮箱在 100 个目标的检查；实际注册情况和公开资料情况尚未独立确认。站点隐藏占用状态、验证码、浏览器安全挑战、服务业务错误、HTTP 拒绝或连接失败时仍无法完成状态判定；部分协议的阳性、限流等分支尚无对应真实样本 |
| 变化分类 | 消失与正文内容变化仍需具备对应真实变化的跨时段记录验证 |

邮箱能力保持待验收状态；接口返回限流、阻断或未知时不会据此宣称邮箱未注册。站点目录中的 `supported` 表示规则可被当前执行器解释，实际覆盖率和准确率需要按站点测量。

## 复现命令

本地和公开网络检查：

```bash
uv run ruff check src tests scripts
uv run pytest tests/test_core.py tests/test_mcp.py tests/test_live.py::test_public_request_dedup_and_restart tests/test_live.py::test_public_collectors
```

完整本地回归执行 `uv run pytest -m "not live"`。真实存档回归使用以下本机环境变量，测试以只读方式复制数据库和所需原始资料至忽略目录：

| 环境变量 | 已有真实资料 |
| --- | --- |
| `KUDO_CAPTURED_COLLECTORS` | 包含公开账号、对应主页与 IP 的采集数据库 |
| `KUDO_CAPTURED_RESEARCH` | 包含规则冲突、邮箱检查及失败动作的研究数据库 |
| `KUDO_CAPTURED_DOMESTIC_PROFILES` | 国内公开主页采集目录 |
| `KUDO_CAPTURED_DOMESTIC_EMAIL` | 包含 `gitee/` 与 `tencent-cloud/` 的国内邮箱采集目录 |
| `KUDO_CAPTURED_EMAIL_EXPANSION` | 新增提供方的真实采集目录；多个目录用操作系统的 `os.pathsep` 分隔，测试递归读取 `research.sqlite` 及原始资料 |
| `KUDO_CAPTURED_EMAIL_COMMUNITIES` | 73 个社区的真实采集目录，包含 `research.sqlite` 和 `runs/`；每个社区对应两个不同的自有邮箱 |

未提供对应路径时，仅跳过依赖该组私有存档的测试。已配置的邮箱存档测试会核对 HTTP 状态、原始 JSON 与证据状态、两个不同邮箱的查询记录及所有原始资料校验值；接口响应核对与账户准确率分别记录。

配置模型和自有邮箱样本以后执行：

```bash
uv run kudo --env-file .env doctor --online
uv run --env-file .env pytest -m live
```

`KUDO_EMAIL_SAMPLES` 指向本地 JSON 文件，对象列表中的每项包含 `site`、`email`、`owned: true`。账户检查填写 `registered: true/false`，公开头像或资料检查填写 `observed: true/false`。未知答案可以保留 `null` 并执行 `scripts/probe_email.py` 收集响应；准确性验收要求相应答案已经确认。凭据、邮箱和运行资料保存在 Git 忽略目录。
