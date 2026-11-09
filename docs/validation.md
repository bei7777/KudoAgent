# 验证记录

日期：2026-11-10。环境：macOS、Python 3.12.11。依赖依据 `uv.lock`，包括 LangGraph 1.2.20、MCP Python SDK 2.3.0、HTTPX 0.28.1、Pydantic 2.14.3。

## 已执行检查

| 检查 | 实际结果 |
| --- | --- |
| Python 静态检查 | `ruff check src tests scripts` 通过 |
| 本地测试 | 55 项通过；使用真实规则文件、phonenumbers 数据、本地 SQLite、官方 MCP 客户端、真实 Starlette 应用和已有采集记录；包含多行输入准备、去重、逐行错误、默认全部范围、中文搜索、国内站点筛选、证据分类及引用定位 |
| 输入与资料分类 | 本地准备接口自动识别线索，研究问题可以留空；预览和无效提交不创建任务。真实记录验证候选 URL 去重计数、多条证据保留、规则冲突、邮箱未知状态与号码资料分类 |
| 默认查询范围 | 实际加载并持久化 8,303 项初始用户名查询；首批选择 50 项，模型规划调用数为 0，关闭后重新打开 SQLite 能读取完整队列与批次决定 |
| 国内站点目录 | 上游 `cn` 标签对应 137 项，其中 131 项已适配；中文搜索能够定位知乎、豆瓣、博客园、微博和哔哩哔哩；受限规则显示输入类型或会话限制 |
| 国内站点实际查询 | Gitee 的 `oschina` 公开页面返回 HTTP 200，两套规则均得到 `detected`，共用一次网络请求；原始响应、规则结果与模型报告已保存 |
| 公开网络测试 | 2 项通过；公开 GitHub 项目 API、Sherlock 项目账号与主页、公共 IP `1.1.1.1` |
| 请求共享与恢复读取 | 同一 GitHub API 请求并发调用仅发送 1 次；关闭并重新打开存储后复用资料；不同参数产生独立请求 |
| 连接失败隔离 | 使用获授权的真实用户名执行前 100 项目录查询，91 项返回结果、9 项连接失败；失败记录包含请求地址和底层异常，第二批继续执行；恢复读取保留已处理查询，没有重复发送其请求 |
| 查询限制隔离 | 真实地址解析检查拒绝回环地址，号码资料查询继续完成，网络请求数为 0；恢复保留拒绝记录。实际站点查询中，HubPages 的非公共地址解析被单独标记为受限，其余站点继续执行 |
| 服务端 Cookie 限制 | Psychologies 的实际响应包含请求头无法使用的 Cookie 字符，查询明确记录为受限；同一 HTTP 客户端随后访问 Gitee 返回 HTTP 200 |
| 页面检测 | GitHub 账号与主页返回 `detected`，分别保留两套规则、页面资料、外链与标识符提及 |
| 号码资料 | phonenumbers 官方示例号码返回 CH 地区资料；本地处理没有网络请求 |
| IP 资料 | ipwho.is 返回 `metadata`，保存完整 JSON、坐标及来源 |
| 来源完整性 | 原始资料校验、证据引用验证、重复动作识别和请求预算检查通过 |
| 结果比较 | 两次实际采集正确区分新增 IP 资料、受限页面、没有再次查询的主页与保持一致的号码资料；没有把受限页面记为消失 |
| MCP 通信 | 官方客户端完成初始化、9 个工具发现、能力查询、结构化结果读取和配置缺失错误检查 |
| Web 页面与访问控制 | 实际应用返回首页、脚本、样式与图标；Host、Origin、页面会话令牌、输入校验、路径限制和服务重启后令牌失效检查通过 |
| Web 真实浏览器流程 | Chrome 通过真实 DeepSeek 服务完成两次公开 Sherlock 项目主页研究；多行输入、选填问题、草稿保留、中文搜索、国内筛选、分页、默认全部范围、覆盖进度、键盘操作、资料分类与搜索、原始资料 SHA-256 校验、报告下载、引用定位和返回、取消恢复、结果比较以及 320、390、760、1024、1440 像素宽度布局检查通过；浏览器脚本错误为 0 |
| 邮箱目录 | CLI 与 MCP 共用 13 个提供方，包含博客园和京东；能力列表提供查询含义、验证日期与最近实测状态 |
| 邮箱接口观察 | 两个自有邮箱覆盖 13 个提供方；WordPress.com、博客园、Gravatar、Duolingo 取得相应查询的明确响应，其余结果为访问限制、未知或连接失败；未计算账户准确率 |
| 模型连接 | DeepSeek `deepseek-flash` 的真实结构化工具调用检查通过 |
| 自主研究 | 真实 DeepSeek 服务完成公开 GitHub 项目研究，生成报告并通过证据引用校验 |
| MCP 研究、取消与恢复 | 官方客户端通过真实 DeepSeek 服务创建研究，取得证据后取消任务；重新启动 MCP 服务并恢复任务，既有证据完整保留，报告生成成功 |
| 进程中断恢复 | 采集期间、模型报告生成期间和模型规划期间分别强制终止测试进程，再启动新进程恢复；3 项测试通过，已保存证据与响应保持完整 |
| 预算与模型记录 | 中断的规划调用计入次数预算；恢复后保留已保存耗时；原始模型请求、返回和服务端 token 用量持久化 |
| 构建 | wheel 与源码分发包构建成功，包含规则、来源记录和许可证 |
| 独立安装 | wheel 安装至干净 Python 环境后，包从 `site-packages` 加载；规则、来源记录、完整许可、CLI、官方 MCP 客户端读取 9 个工具与 Web 页面资源检查均通过 |

本地运行记录保存在 Git 忽略目录 `.runtime/`。`redesign-offline.xml` 保存 55 项本地测试结果；`redesign-browser/results.json` 保存真实浏览器流程的两项研究标识与检查结果；`all-scope-live.xml` 保存完整范围持久化、真实模型研究、MCP 取消恢复与三种进程中断恢复的 6 项通过记录；`domestic-verification/` 保存 Gitee 的实际查询记录与报告；`comparison-results.xml` 保存两次实际采集的比较结果。公开网络测试保留默认 20 秒请求期限，执行结果受当前网络连接与站点响应影响。

完整目录测试验证了规则加载、初始范围和持久化。实际网络验证覆盖本页记录中的具体站点；8,303 项规则尚未逐项完成在线有效性测量。

连接失败检查记录保存在 `.runtime/network-isolation-result.json` 与 `.runtime/network-isolation/`，本地检查结果保存在 `.runtime/network-fix-offline.xml`。`.runtime/target-rejection.xml` 保存地址拒绝、服务端 Cookie 限制与独立查询的 2 项通过记录，`.runtime/network-recovery.xml` 保存 4 项真实 MCP 与进程中断恢复通过记录。运行资料包含用户的查询输入，保持 Git 忽略。

两个用户明确授权的自有邮箱均完成了 13 个提供方的检查尝试。逐站结果见[邮箱站点](email-providers.md)，其中公开头像和资料查询保留各自含义。原有提供方的记录保存在 `.runtime/email-checks/` 与 `.runtime/email-checks-current/`，新增提供方记录保存在 `.runtime/email-expanded/`；各次执行的 `email-check.json`、SQLite 与原始响应可相互核验。具体邮箱及结果索引保存在 `private/email-results.md`。

## 仍需完成的验收

| 验收项 | 所需条件与当前状态 |
| --- | --- |
| 邮箱准确性与受限接口 | 已执行两个自有邮箱在 13 个提供方的检查；实际注册情况和公开资料情况尚未独立确认。验证码、浏览器安全挑战、HTTP 拒绝或连接失败的站点仍无法完成状态判定 |
| 变化分类 | 消失与正文内容变化仍需具备对应真实变化的跨时段记录验证 |
| Linux、Windows | 已提供三种操作系统的 GitHub Actions 检查配置；当前环境尚未实际运行 Linux、Windows 检查 |

邮箱能力保持待验收状态；接口返回限流、阻断或未知时不会据此宣称邮箱未注册。站点目录中的 `supported` 表示规则可被当前执行器解释，实际覆盖率和准确率需要按站点测量。

## 复现命令

本地和公开网络检查：

```bash
uv run ruff check src tests scripts
uv run pytest tests/test_core.py tests/test_mcp.py tests/test_live.py::test_public_request_dedup_and_restart tests/test_live.py::test_public_collectors
```

配置模型和自有邮箱样本以后执行：

```bash
uv run kudo --env-file .env doctor --online
uv run --env-file .env pytest -m live
```

`KUDO_EMAIL_SAMPLES` 指向本地 JSON 文件，对象列表中的每项包含 `site`、`email`、`owned: true`。账户检查填写 `registered: true/false`，公开头像或资料检查填写 `observed: true/false`。未知答案可以保留 `null` 并执行 `scripts/probe_email.py` 收集响应；准确性验收要求相应答案已经确认。凭据、邮箱和运行资料保存在 Git 忽略目录。
