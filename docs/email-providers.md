# 邮箱站点

邮箱采集默认查询全部 13 个邮箱站点，CLI 与 API 可通过 `email_sites` 限定范围。每个提供方保存交互类型、结果含义、原始响应和规则版本。CLI `kudo email-sites` 与 MCP `list_capabilities` 返回同一份目录，并附带最近验证日期与观察结果。

## 接入与实际观察

2026-11-09，在 macOS 环境中对两个获授权的自有邮箱执行检查。下表记录接口返回；样本的实际账户情况尚未独立确认，准确率保持 `not_established`。公开目录与安装包不包含样本邮箱。

| 站点标识 | 提供方 | 检查内容 | 两个样本的实际观察 |
| --- | --- | --- | --- |
| `cnblogs` | 博客园 | 注册表单的独立邮箱字段校验 | HTTP 200、JSON `true`；接口表示可用于注册 |
| `jd` | 京东 | 注册表单的独立邮箱字段校验 | HTTP 200、`resultCode: 608`；需要有效验证码，状态为 `blocked` |
| `github` | GitHub | 注册表单的邮箱字段校验 | 引导页面 HTTP 403，状态为 `blocked` |
| `pinterest` | Pinterest | 注册页面的账户存在性查询 | 资源接口 HTTP 403，状态为 `blocked` |
| `spotify` | Spotify | 注册接口的邮箱字段校验 | 当前网络环境的 TLS 连接失败，任务保存实际错误 |
| `mozilla` | Mozilla | 账户状态查询 | HTTP 406、空正文，状态为 `unknown` |
| `wordpress` | WordPress.com | 查询可用登录方式 | 返回 `unknown_user`，状态为 `not_detected` |
| `gravatar` | Gravatar | 邮箱 SHA-256 摘要对应的公开头像 | 默认显示等级 `g` 下 HTTP 404；没有发现头像，注册状态保持未知 |
| `duolingo` | Duolingo | 邮箱对应的可访问用户资料 | HTTP 200、空 `users` 列表；当前查询没有返回资料，注册状态保持未知 |
| `replit` | Replit | 账户存在性查询 | HTTP 403，状态为 `blocked` |
| `imgur` | Imgur | 注册页面的邮箱可用性检查 | HTTP 412，安全检查未通过，状态为 `blocked` |
| `lastfm` | Last.fm | 注册表单的独立邮箱字段校验 | 页面 HTTP 200，内容为浏览器安全挑战，状态为 `blocked` |
| `freelancer` | Freelancer | 用户字段检查 | HTTP 403，状态为 `blocked` |

`not_detected` 的含义由提供方的 `interaction`、`meaning` 和证据摘要限定。Gravatar 和 Duolingo 的结果包含 `registration_status: unknown`。邮箱字段校验通过只表达该接口当时的返回，账户归属仍需要独立证据。

本项目调用账户状态、字段校验和公开资料接口。接口要求验证码时记录阻断；执行流程不调用发送验证码、重置密码或创建账户的接口。

## 使用

查看当前目录：

```bash
uv run kudo email-sites
```

使用本机已设置的 `OWN_EMAIL` 环境变量查询两个中国站点：

```bash
uv run kudo --env-file .env research "检查我的邮箱在指定站点的注册迹象，逐项说明来源与限制" --email "$OWN_EMAIL" --email-site cnblogs --email-site jd
```

MCP 的 `start_research` 接收相同的 `ResearchRequest` 字段，其中 `email_sites` 可填写站点标识。省略该字段或传入空列表时，应用采用全部邮箱查询站点。

## 真实样本与验收

样本保存在 Git 忽略的 `private/email-samples.json`。每项包含 `site`、`email`、`owned: true`。账户状态检查使用 `registered`，公开头像或资料检查使用 `observed`，值为已确认的 `true` 或 `false`；未知时保留 `null`。

`registered` 与 `observed` 各自描述已知事实，程序不会用一次查询结果自动填写样本答案。完整准确性验收要求相应字段已经确认。单项检查允许未知答案，并保存实际响应：

```bash
uv run python scripts/probe_email.py --samples private/email-samples.json --sample-index 0 --data-dir .runtime/email-checks
```

私有查询记录包含响应正文、时间、SHA-256、HTTP 状态和证据；公开验证摘要保存在包内 `research_core/data/email-verification.json`。其中 `last_check` 为带日期的验证快照，当前查询结果以实际响应为准。

## 接口依据

各适配器独立编写。Holehe 的功能分类用于确定候选能力；没有复制其 GPL 实现或规则数据。研究依据固定于 `megadose/holehe` 提交 `14da70f588538936b20d238783c5e28a0772a2b3`，涉及 `holehe/modules/software/firefox.py`、`cms/wordpress.py`、`cms/gravatar.py`、`learning/duolingo.py`、`programing/replit.py`、`social_media/imgur.py`、`music/lastfm.py`、`jobs/freelancer.py`。

中国站点依据当前官方客户端声明的请求独立实现：

- 博客园：[官方注册页面](https://account.cnblogs.com/signup)加载的 [账户字段校验模块](https://account-cdn.cnblogs.com/517-es2015.7d9de2f06bc965c7dad4.js)通过 `checkEmail` 和 `remoteValidation` 使用 multipart POST 请求 `/account/checkEmail`。返回 `true` 表示字段校验通过，文字响应表示具体错误。
- 京东：[官方注册页面](https://reg.jd.com/reg/person)加载的 [注册模块](https://storage.jd.com/retail-mall/jdc_user_register/pc/user/register/0.0.10/js/personal/register.js)声明 `/p/checkEmail`，`Da` 处理 `resultCode`。适配器读取页面生成的 `uuid`、`source`、`eid` 与令牌，保留接口给出的验证码限制。
- Mozilla：[官方 API 文档](https://mozilla.github.io/ecosystem-platform/api)定义 `/v1/account/status` 的邮箱输入及 `exists` 字段。
- Gravatar：[官方头像文档](https://docs.gravatar.com/sdk/images/)定义邮箱 SHA-256 摘要和 `d=404` 的缺失头像行为。

其余提供方的官方页面链接由 `email-sites` 返回。接口数据结构、本地协议构造或程序配置发生异常时，任务立即失败并保存原因。连接失败和无法处理的服务端响应结束对应查询，保存具体原因，其余独立查询继续执行；页面访问限制以对应观察状态记录。
