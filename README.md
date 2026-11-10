# KudoAgent

使用 Python 与 LangGraph 进行公开足迹研究，提供本机 Web 工作台、`kudo` 命令和 `kudo-mcp` 本地 stdio MCP 服务。输入用户名、公开主页、邮箱、电话号码或公共 IP，按目标安排查询并生成带来源的 Markdown / JSON 报告。

相同请求共用原始响应，Sherlock 与 Maigret 的检测结果分别保留。Agent 为每项动作说明待解决的问题，程序检查范围与预算。研究记录可以恢复、比较，并补充用户明确给出的关系确认。

## 安装

需要 Python 3.12 或更高版本。

```bash
uv sync --locked
uv run kudo --help
uv run kudo doctor
```

构建与独立安装：

```bash
uv build
uv tool install ./dist/kudo_agent-0.1.0-py3-none-any.whl
kudo --help
```

安装包包含固定版本的规则数据与版权声明。目标系统为 macOS、Linux、Windows；实际验证范围见 [验证记录](docs/validation.md)。

## Web 工作台

填写下方说明中的 `.env` 后，在项目目录启动：

```bash
uv run kudo --env-file .env web
```

在浏览器打开 `http://127.0.0.1:8765`。已安装的 wheel 也支持 `kudo --env-file /完整路径/.env web`。通过 `--port 8766` 可以选择其他本机端口。

- 输入线索：首页直接粘贴用户名、主页链接、邮箱、完整电话号码或公共 IP，每行一条，最多 20 条；自动显示类型、合并重复线索，并提示无效输入。中国大陆手机号可直接填写 11 位号码，支持 `86`、`+86`、空格及横线；其他国际号码填写 `+` 和国家代码。数字用户名可添加 `username:` 前缀。研究问题选填，运行预算可以展开调整。
- 查询范围：根据线索类型执行全部适配规则，用户名查询先执行 131 个国内适配站点，完整范围保留 8,303 个条目；邮箱查询使用全部 27 个已接入站点，7 个中国站点优先执行，无需选择站点。
- 发现资料：研究默认打开资料列表，按候选页面、邮箱检查、号码与 IP、待核查和全部记录分类；支持搜索站点、线索、地址或摘要。
- 核查来源：候选页面按 URL 去重计数，同一页面的多条证据与规则冲突分别保留。展开记录可查看检测规则、关系来源和带 SHA-256 的原始响应，并下载完整资料。
- 执行记录：查看初始范围、待执行数量、预算、耗时、查询理由和连接失败详情。
- 研究报告：从每条结论跳转到对应证据，下载 Markdown 或 JSON。
- 任务管理：搜索历史任务、取消研究、恢复任务、重新研究、确认已有资料之间的关系。
- 结果比较与站点目录：对照两次研究，并查看提供方的实际查询含义与验证状态。

分类数量表示整个任务的统计，搜索和分页仅改变当前显示的记录。同名、页面外链、标识符提及和用户确认分别保留；邮箱检查区分账户状态与公开头像、资料，号码搜索建议单独列示。

工作台与 CLI、MCP 使用同一份本机配置和研究目录。服务绑定 `127.0.0.1`，模型凭据保留在服务进程中。关闭浏览器后，服务继续执行；在启动终端按 `Ctrl+C` 结束服务，未完成的任务保留恢复所需记录。

操作说明和验证方法见 [Web 工作台](docs/web.md)。

## 模型配置

采用 OpenAI 兼容 Chat Completions 与结构化工具调用。示例配置 `.env.example` 使用 DeepSeek：

| 配置 | 含义 |
| --- | --- |
| `KUDO_BASE_URL` | 服务地址，例如 `https://api.deepseek.com` |
| `KUDO_MODEL` | 模型名称，例如 `deepseek-flash` |
| `KUDO_API_KEY_ENV` | 密钥所在的环境变量名称，默认 `KUDO_API_KEY`；示例采用 `DEEPSEEK_API_KEY` |
| `KUDO_ENV_FILE` | 可选，显式加载的本地 dotenv 文件路径 |
| `KUDO_DATA_DIR` | 可选，任务与证据目录 |

将密钥保存在使用者自己的环境变量或本地 dotenv 文件中。`.env.example` 保存公开配置；项目忽略 `.env`、`private/` 和 `.runtime/`。环境变量优先于 dotenv 文件。

复制 `.env.example` 为 `.env`，填写其中的 `DEEPSEEK_API_KEY` 即可使用示例服务。

网络连接遵循 HTTPX 的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 和 `NO_PROXY` 配置，安装依赖包含 SOCKS 支持。具体字段见 [HTTPX 环境变量说明](https://www.python-httpx.org/environment_variables/)。

```bash
uv run kudo --env-file .env doctor --online
```

DeepSeek 使用官方 `/beta` 接口的严格工具调用模式，并关闭思考模式以支持指定工具调用。Pydantic 生成接口支持的参数结构，本地继续校验完整约束。接口要求见 [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/) 和 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。其他兼容服务使用通用请求参数。模型返回错误结构时，任务立即失败并保存错误。

## 使用

限定站点查询公开项目账号：

```bash
uv run kudo --env-file .env research "核查 Sherlock 项目的公开主页并引用来源" --username sherlock-project --site GitHub
```

也可以直接在目标文字中写出标识符，由模型提取。需要完整预算控制时，使用 [研究请求示例](examples/research.json)：

```bash
uv run kudo --env-file .env research "公开项目研究" --request-file examples/research.json
```

| 命令 | 用途 |
| --- | --- |
| `sites --query 知乎` | 按中文名称、英文名称或域名查找站点与规则适配情况 |
| `sites --region CN` | 查看上游规则标记为中国地区的站点 |
| `email-sites` | 查看邮箱查询站点、查询含义与最近实测状态 |
| `status RUN_ID` | 查看任务状态、请求数量和证据数量 |
| `evidence RUN_ID --offset 0 --limit 50` | 分页读取完整证据 |
| `report RUN_ID --format json` | 导出结构化报告，默认格式为 Markdown |
| `cancel RUN_ID` | 取消正在运行的任务 |
| `resume RUN_ID` | 从保存的检查点恢复 |
| `compare PREVIOUS_ID CURRENT_ID` | 比较两次研究的变化 |
| `confirm RUN_ID SOURCE_URL TARGET_URL "确认声明"` | 记录用户对已有资料之间关系的明确声明 |

用户名查询默认使用全部 8,303 个已适配目录条目，先执行 131 个国内适配站点。Gitee、CSDN、知乎、豆瓣、百度贴吧、V2EX、博客园与掘金优先排列，初始范围每批执行 50 项；随后由模型安排关联核验。CLI 的 `--site` 和 API 的 `sites` 可用于明确限定查询范围，显式指定的站点按输入顺序执行。Web 新建研究自动采用全部适配站点。目录提供实际输入要求：掘金和网易云音乐需要数字用户 ID，Bilibili 需要带 UID 的完整主页链接，微博保留访客会话限制。抖音、小红书和快手接收完整公开主页链接，采集范围取决于页面公开程度与访问限制；TikTok 使用独立的用户名规则。

邮箱查询提供 `--email` 即可默认查询全部 27 个邮箱站点，`--email-site` 可以限定范围。中国站点包括腾讯云、华为云、Gitee、坚果云、Zoho 中国站、博客园和京东；其他平台包括 Microsoft、GitHub、X、Adobe、Spotify、Pinterest、WordPress.com、Mozilla、Gravatar、Duolingo、eBay、SoundCloud、Flickr、Replit、Imgur、Last.fm、Freelancer、Plurk、Komoot 和 Internet Archive。运行 `uv run kudo email-sites` 查看标识、查询含义与最近实测状态。Web 目录支持名称搜索和中国站点筛选。

邮箱结果分别展示计划查询数、已返回结果数、连接失败、查询受限、执行失败和待处理数量。一个站点检查多个邮箱时会产生多项查询，站点数量单独去重统计。各站点的交互含义与实测结果见 [邮箱站点](docs/email-providers.md)。

CLI 电话号码使用 `--phone` 并包含 `+` 和国家代码；Web 支持直接识别中国大陆手机号。号码模块在本地输出标准格式、号段地区、原始运营商、号码类型和时区。有效性表示号码符合分配规则；当前启用、可接通状态、持有人及实时位置均未核实，携号转网也可能改变当前运营商。

中国号码提供百度、Google 与 Bing 的多种格式搜索链接，其他号码提供 Google 与 Bing 搜索链接。链接标记为 `not_executed`，需要打开后查看实际搜索结果。已有主页中出现已知号码时，程序使用 phonenumbers 精确匹配，保存正文或 `tel:` 链接中的位置、上下文与来源。IP 使用 `--ip`，返回网络资料与估算位置。

## MCP

使用 [MCP 配置示例](examples/mcp.json)，将命令改为已安装的 `kudo-mcp`，将 dotenv 路径改为本机已有配置文件。也可以直接启动：

```bash
uv run kudo --env-file .env mcp
```

MCP 工具包括 `list_capabilities`、`start_research`、`get_status`、`get_evidence`、`get_report`、`cancel_research`、`resume_research`、`compare_research`、`record_confirmation`。研究在 MCP 进程内后台执行，创建操作返回 `run_id`。客户端退出后任务保存为中断状态，重新连接后可恢复。

服务通过 stdio 与本地客户端通信。模型凭据由服务进程环境提供，MCP 工具参数只接收研究数据。日志写入标准错误。

## 数据与恢复

默认使用操作系统的用户应用数据目录。`--data-dir` 可以指定其他位置。目录内的 `research.sqlite` 保存研究资料，`checkpoints.sqlite` 保存 LangGraph 状态；每项研究的原始响应与报告保存在 `runs/RUN_ID/`。

每份原始响应具有 SHA-256 校验值。恢复后复用已经完成的动作和请求；未确认完成的账户接口请求需要显式使用 `--retry-interrupted-accounts`。取消及运行期限会终止活动请求。规则版本变化时需创建新研究，以保持证据与执行规则一致。

`status` 返回模型调用次数、规划尝试次数和已取得的 token 用量。原始模型请求与返回保存在本地 SQLite；中断的规划调用仍计入预算。运行耗时每 250 毫秒保存一次。

默认预算：全部已适配站点、并发 10 项请求、每个域名并发 2 项、请求期限 20 秒、研究期限 7,200 秒、12 轮关联规划、50,000 次网络请求、最多 2 层关联扩展。初始范围的分批执行独立于模型规划轮数。重定向计入请求数，模型调用受规划轮数与运行期限约束。

`status` 中的 `coverage` 单独记录初始范围的计划数量、已返回结果数量、连接失败数量、查询受限数量和待执行数量。连接失败的查询保存实际地址及底层错误；非公共地址、响应大小与跳转限制记录为查询受限。这些查询的账户状态保持未知，其余独立站点继续执行。恢复任务复用已返回的结果与失败、受限记录。预算耗尽或任务中断时，尚未执行的查询继续显示为待执行；已返回的结果也可能为未知、限流或阻断。

`sites` 中的 `supported` 表示规则能够由当前执行器解释。站点实际在线可用性、账户判定准确率与规则数量分别评估。

## 开发与验证

| 模块 | 职责 |
| --- | --- |
| `research_core` | 输入校验、合并站点目录、共享请求、各类采集 |
| `research_agent` | LangGraph 流程与结构化模型决策 |
| `evidence` | SQLite 记录、来源检查、报告与变化比较 |
| `application` | 后台任务、取消恢复、公共接口 |
| `integrations` | CLI、MCP、Web API 与页面资源 |
| `upstream` | 固定代码来源、规则快照与许可证 |

```bash
uv run ruff check src tests
uv run pytest -m "not live"
uv run pytest tests/test_live.py -k public
```

完整在线验收执行 `uv run --env-file .env pytest -m live`，要求已有模型配置与 `KUDO_EMAIL_SAMPLES` 指向的自有邮箱样本 JSON。样本为对象列表，每项填写 `site`、`email`、`owned: true`。账户检查填写已确认的 `registered: true/false`；Gravatar、Duolingo 的公开资料检查填写已确认的 `observed: true/false`。凭据或样本答案缺失会使对应验收失败，验证记录保持实际状态。

已提供自有邮箱、尚不确定注册状态时，可将 `registered` 留为 `null`，使用单项核查脚本保存当前响应：

```bash
uv run python scripts/probe_email.py --samples private/email-samples.json --sample-index 0 --data-dir .runtime/email-checks
```

该脚本保存实际观察，样本答案由独立确认的账户或公开资料情况提供。

独立仓库包含 macOS、Linux、Windows 的 GitHub Actions 检查配置，执行本地测试、构建与干净环境安装验证。实际运行结果仍以验证记录为准。

详细执行流程见 [架构说明](docs/architecture.md)。

## 许可证

项目采用 MIT。Sherlock 和 Maigret 的选取代码与规则保留上游 MIT 声明；来源信息记录于包内 `upstream/PROVENANCE.json`。[第三方说明](THIRD_PARTY_NOTICES.md)列出固定提交、复用内容及依赖许可。
