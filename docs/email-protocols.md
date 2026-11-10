# 邮箱检查协议

## 华为云

来源为[官方注册页面](https://reg.huaweicloud.com/registerui/register.html)及其[当前注册客户端](https://res.hc-cdn.com/CBC-CBCCustomerRegWebsite/11.10.221/vue3/js/application/usecase/core/register/Type.js)。`getEmailUsedStage` 调用 `getUsedStage`，使用 `checkIdentity` 检查邮箱字段；`extractUsedStage` 分别读取是否使用与是否允许注册。实现位于 `research_core/email_domestic.py:huawei_cloud`。

读取 `/registerui/cn/index.html` 建立匿名会话后，请求 `POST /registerui/rest/cbc/cbccustomerregservice/v1/account/check_identity`。JSON 中使用 `identityType=EMAIL`、`identityValue`、`scenceType=register_portal`、`areaCode=CN` 和当前页面的 `clientInfo`；请求头保留客户端声明的站点区域、语言及页面会话信息。该请求仅检查字段。

`identityUsed="true"` 表示邮箱已关联账户；`identityUsed="false"` 且 `canUse="true"` 表示接口未发现关联并允许继续注册。`canUse="false"` 单独出现时保持未知。证据属性分别保存 `identity_used` 和 `registration_available`；原始响应也保留在任务资料中。

2026-11-10 对两个获授权自有邮箱完成标准采集，各执行两次 HTTP 请求，均返回 `200 / 200` 和未发现状态。采集记录通过资料校验。样本没有提供已知注册真值，本次记录验证了接口响应与证据保存流程。

## 坚果云

来源为[官方注册页面](https://www.jianguoyun.com/d/signup)及其[当前登录注册客户端](https://www.jianguoyun.com/static/js/login_signup.min-ShwOci3LyyMcyqlK5bwqIw.js)。客户端在邮箱步骤调用 `verifyEmail`，随后根据结果显示手机号或密码步骤。实现位于 `research_core/email_domestic.py:jianguoyun`。

读取注册页面后，请求 `GET /d/ajax/getCaptcha`。服务器允许匿名字段检查时，使用其返回的会话字段向 `POST /d/ajax/userop/verifyEmail` 提交邮箱。`needRealCaptcha=true` 时记录阻断并终止当前站点的检查，此时邮箱尚未提交。接口没有执行发送验证码或注册操作。

`isNotReg=true` 表示邮箱可用于注册；`isNotReg=false` 对应官方页面的邮箱已注册提示。字段值必须为布尔值，返回格式变化会暴露为协议错误。

2026-11-10 两个自有邮箱的独立协议验证均得到 `200 / 200 / 200`、`isNotReg=true`。随后通过标准采集器验证时，一个样本仍返回未发现，另一个在第二次请求返回人工验证码要求并记录阻断。两种标准采集结果均通过资料校验。目录展示的最近状态包含这次访问限制。

## Zoho 中国站

来源为[官方注册页面](https://accounts.zoho.com.cn/register)、页面声明的[注册配置脚本](https://accounts.zoho.com.cn/register/script?loadcss=false&tvisit=true&isAccountsSignup=true)及[当前字段校验客户端](https://static.zohocdn.com.cn/iam/accounts/js/signup-new.min.87022b45a6613f69fcdb6d3c5b55e019.js)。客户端的 `validateRemote` 在邮箱字段校验中调用独立接口。实现位于 `research_core/email_domestic.py:zoho_cn`。

读取页面声明的配置脚本后，通过 Esprima 解析 JavaScript 语法树，取得匿名会话凭据。脚本内容不会执行。向 `POST /webclient/v1/register/field/validate` 提交 `signupvalidate.email`、服务名称与注册页面地址，并使用服务返回的匿名凭据。该请求仅进行字段校验。

`status_code=201`、`resource_name=signupvalidate` 且没有错误时，记录邮箱字段允许继续注册。其他业务错误保留错误代码和未知状态。当前实现没有将未验证的业务错误解释为已注册。

2026-11-10 两个获授权自有邮箱均完成标准采集，各执行三次 HTTP 请求，均返回 `200 / 200 / 200` 和字段校验成功，结果记录为未发现。采集记录通过资料校验。本次样本没有覆盖已知注册邮箱的阳性响应。

## Microsoft

来源为 [Microsoft 官方个人账户登录页面](https://login.live.com/)及其[当前登录客户端](https://logincdn.msauth.net/shared/5/js/login_v2_en_mtvO72wL-ZeQU9Cu6cq5PQ2.js)。客户端的 `Nb` 构造登录方式查询参数，`$b` 请求页面声明的 `getCredentialTypeUrl`；`IfExistsResult` 枚举给出查询结果的含义。实现位于 `research_core/email_products.py:microsoft`。

使用 chompjs 解析页面 `ServerData`，读取 `urlGetCredentialType` 和隐藏的 `PPFT` 流程令牌。接口必须为 HTTPS `login.live.com/GetCredentialType.srf`。请求包含邮箱与当前流程令牌，并明确设置 `otclogindisallowed=true`、`noPaOtcDisallowed=true`、`forceotclogin=false`、`isRemoteNGCSupported=false`，关闭自动验证码与远程认证请求。采集仅执行登录方式查询。

`IfExistsResult=1` 表示未发现；`0/5/6/8` 分别表示当前 Microsoft 身份提供方、其他 Microsoft 身份提供方、两种身份提供方或 ACMA 身份提供方存在记录。结果保存原始代码与查询范围，账户类型保持各自含义。`2` 表示限流，`-1/4` 保持未知，未定义状态触发协议错误。

`ThrottleStatus=1/2` 表示部分身份提供方受到限流，此时保留查询受限状态，账户情况尚未完整确认。该状态与原始结果代码一同保存。

2026-11-10 两个获授权自有邮箱均完成 `200 / 200` 标准采集，分别观察到未发现和发现，证据与原始资料校验通过。样本没有提供独立的已知注册真值。

当日后续实时复测在初始化页面发生连接超时，没有再次提交邮箱。原有成功响应未提供 `ThrottleStatus`；部分身份提供方限流分支仍缺少对应的真实限流样本。

## Adobe

来源为 [Adobe 官方账户页面](https://account.adobe.com/)、其[当前登录客户端](https://auth.services.adobe.com/assets/index-5rGnnAQU.js)及[账户 API 定义](https://auth.services.adobe.com/assets/progressive-profile-DFEYc-g9.js)。客户端的 `getAccountsV2` 调用 `getUserAccountsUsingPOST`，将返回的账户类型和登录方式用于后续界面选择。实现位于 `research_core/email_products.py:adobe`。

向 HTTPS `auth.services.adobe.com/signin/v2/users/accounts` 发送 `POST`，JSON 包含 `username` 和 `usernameType=EMAIL`；`X-IMS-CLIENTID` 使用官方账户页面的公开客户端标识 `SunbreakWebUI1`。接口读取账户类型，不发送验证消息。

返回空数组时记录未发现关联账户。个人或企业账户记录包含登录方式时记录发现；联合身份或组织登录配置单独返回时保持未知，并保存账户类型。响应必须符合账户列表结构，格式变化立即暴露。

2026-11-10 两个获授权自有邮箱均获得 HTTP `200` 和空账户数组，标准采集结果为未发现，资料校验通过。本次样本没有覆盖已知注册账户的阳性响应。

## eBay

来源为 [eBay 官方登录页面](https://signin.ebay.com/signin)及其[当前登录客户端](https://ir.ebaystatic.com/rs/c/signin-render-0mV8CE6r.js)。客户端在邮箱步骤调用 `defaultIdentifierServiceUrl`，使用 `identifier`、`csrfAjaxToken` 及登录方式检查参数，随后处理 `userType` 和错误提示。实现位于 `research_core/email_products.py:ebay`。

使用 chompjs 解析页面组件配置，读取独立字段检查地址与 CSRF 令牌。目标必须为 HTTPS `signin.ebay.com/signin/srv/identifer`，保留官方路径中的拼写。查询使用 `POST`，禁用 WebAuthn 检查，并终止自动重定向；原始重定向响应会归档。

有效账户类型配合可用登录方式表示发现记录；明确指向注册的错误组合表示未发现。其他错误保持未知。官方安全验证页面、HTTP `403` 或指向 `/splashui/captcha` 的重定向记录为阻断，保留来源资料。

2026-11-10 两个获授权自有邮箱的标准采集均记录阻断：一项返回 `200 / 307` 并要求浏览器验证码，另一项在页面请求返回 `403`。当前环境没有完成注册状态判定。

## X

[官方邮箱可用性接口](https://api.x.com/i/users/email_available.json)使用 `GET` 和 `email` 参数。`taken=true` 表示邮箱已占用，`taken=false` 且 `valid=true` 表示允许继续注册；其他字段校验结果保持未知。实现位于 `research_core/email_social.py:twitter`。2026-11-10 两个自有邮箱均返回 HTTP 200、`taken=true`，标准采集保存为发现记录。

## SoundCloud

来源为[官方登录页面](https://soundcloud.com/signin)、[匿名认证页面](https://secure.soundcloud.com/web-auth)及其[当前认证客户端](https://secure.sndcdn.com/web_auth-5a1b8991cd9f16fb89ac.js)。`verifyIdentifier` 使用独立的 `GET https://api-auth.soundcloud.com/web-auth/identifier`，参数为邮箱 `q` 与页面公开 `client_id`。公共客户端标识通过 chompjs 从页面 `window.__sc_hydration` 配置中读取；`X-Csrf-Token` 使用 Tree-sitter 解析匿名认证页面实际引用的脚本，并读取唯一的请求头配置。页面及脚本一并保存为原始资料。

`type=email` 配合 `status=in_use/available` 分别表示已使用和可用，其他状态保留未知。实现位于 `research_core/email_social.py:soundcloud`。2026-11-10 两个样本的完整标准采集均取得 `200 / 200 / 200 / 403`，最后的邮箱查询要求验证码，状态为阻断。

## Flickr

来源为[官方登录页面](https://identity.flickr.com/login)、[登录客户端](https://identity.flickr.com/js/login.407d75ea.js)及[应用客户端](https://identity.flickr.com/js/app.f2615b08.js)。`checkMigration` 与 `determineLoginFlow` 通过独立 `GET https://identity-api.flickr.com/migration?email=…` 选择后续表单。

`stat=ok` 时保留 `state_code` 和密码登录或账户更新路径，注册情况保持未知。`stat=fail` 时保留业务代码；指向注册页面的状态也不确认账户存在性。实现位于 `research_core/email_social.py:flickr`。2026-11-10 两个样本的标准采集均 HTTP 200、`stat=fail`、`code=11`，账户服务返回应用错误，状态为未知。

## Plurk

来源为[官方页面](https://www.plurk.com/)及其[当前注册客户端](https://s.plurk.com/9c9888b1a86505db4617.js)。`checkEmailAvailable` 使用 `POST /v2/account/validation/email`，表单仅提交 `email`。返回 `status=exist/unused` 分别记录已使用和未发现，`blocked` 保留平台限制，`invalid` 保持账户状态未知。实现位于 `research_core/email_social.py:plurk`。2026-11-10 两个样本均 HTTP 200、`status=unused`，标准采集记录为未发现。

## Komoot

来源为[官方登录页面](https://www.komoot.com/signin)及其[当前账户客户端](https://www.komoot.com/assets/account.2af4607560fc5b1b.js)。邮箱首步使用独立 `POST /v1/signin`，JSON 只包含 `email`；返回 `type=login/register` 决定显示登录表单或注册表单。实现位于 `research_core/email_social.py:komoot`，保存下一步的具体路径类型。2026-11-10 两个样本均 HTTP 200、`type=register`，记录为未发现。

## Internet Archive

来源为[官方注册页面](https://archive.org/signup)、[当前注册客户端](https://archive.org/offshoot_assets/ia-auth-options-CATQd_EL.js)及[会话客户端](https://archive.org/offshoot_assets/csrf-service-CjPtpxs9.js)。`checkAvailability` 使用 `GET /services/csrf-token` 初始化，然后向 `POST /services/account/signup/` 提交 `field=username`、邮箱 `value`、`op=check` 与当前令牌 `t`，请求头保留 `X-CSRF-Token`。

`success=true` 表示邮箱字段校验通过。校验失败时保存未知状态，具体错误可以从原始资料回查。实现位于 `research_core/email_developer.py:archive`。2026-11-10 两个样本均取得 `200 / 200`、`success=true`，标准采集记录为未发现。

## 实现与来源

上述适配器根据当前官方网页声明的请求协议独立编写。Holehe 提供按网站组织邮箱检查、分别表达存在与限流及未知结果的研究参考；产品模块没有复制其 GPL 实现或规则。官方前端文件作为私有研究依据保存，发布包不包含这些前端文件。HTML 与 JavaScript 数据分别使用 Beautiful Soup、chompjs、Esprima 或 Tree-sitter 解析，不执行下载的脚本。各解析依赖的许可与版本列于第三方声明及锁文件。
