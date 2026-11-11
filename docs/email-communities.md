# 社区邮箱检查

邮箱目录共 100 个独立目标：27 个平台接口、20 个 Discuz 社区、8 个 MyBB 社区和 45 个 Discourse 社区。其中 40 个国内及中文目标优先执行。数量表示已接入的查询目标，站点能公开的信息由实际响应决定。CLI、Web 与 MCP 使用同一份目录，默认范围包含全部目标。

## 目标名单

| 协议 | 国内及中文社区 | 其他社区 |
| --- | --- | --- |
| Discuz | 21ic、吾爱破解、A9VG、电子发烧友、恩山、耳机大家坛、全球主机交流论坛、卡饭、其乐、骑砍中文站、数码之家、壬天堂世界、一牛网、52ASUS、智能电视网、野火、瀚思彼岸、正点原子、天使动漫、与非网 | — |
| MyBB | — | BIOS Mods、Blitzortung、Jellyfin、MyBB、NextPVR、ClashFarmer、The CardBoard、Biotechnology Forums |
| Discourse | 小众软件、TiDB、OceanBase、TRAE、MarginNote、SUSE 中文、NVIDIA 中文、计图、openUBMC、Rancher 中文、移远中文、RuyiSDK、动手学深度学习 | OCaml、Haskell、Python、Julia、Ubuntu、NixOS、Home Assistant、OpenWrt、Hugging Face、PyTorch、Elastic、Gradle、Arduino、freeCodeCamp、Ruby on Rails、Django、HashiCorp、Kubernetes、Discourse Meta、Rust、Swift、Kotlin、Go、GNOME、KDE、Fedora、Nextcloud、Syncthing、Godot、Blender、Joplin、Obsidian |

对应的完整地址保存在 `research_core/email_communities.py:COMMUNITIES`，可通过 `kudo email-sites` 或 Web 目录查阅。每个目标拥有独立域名、动作、隔离会话和证据。TiDB 使用其当前官方社区地址 `https://pingkai.cn/tidbcommunity/forum/`；Blitzortung 使用 `/mybb/` 子目录。

## 协议和结果含义

### Discuz

先读取社区入口核实协议，再调用独立的 `GET forum.php?mod=ajax&action=checkemail` 字段接口。其余参数采用客户端声明的 `inajax`、`infloat`、`handlekey` 和 `ajaxmenu`。响应通过 defusedxml 解析 XML，再用 Beautiful Soup 读取消息内容；保留响应编码和原始字节。

`succeed` 表示字段校验通过；明确的邮箱已注册提示表示发现占用记录。邮箱域名限制及其他业务提示保持未知。验证码跳转记录为阻断，字段请求终止自动重定向。

协议依据为 DiscuzX `ccf55748e13c1a5497c08d6eb7b7f29b65a6d2a7` 的 [register.js](https://github.com/DiscuzTeam/DiscuzX/blob/ccf55748e13c1a5497c08d6eb7b7f29b65a6d2a7/upload/static/js/register.js)、[forum_ajax.php](https://github.com/DiscuzTeam/DiscuzX/blob/ccf55748e13c1a5497c08d6eb7b7f29b65a6d2a7/upload/source/module/forum/forum_ajax.php) 和 [function_member.php](https://github.com/DiscuzTeam/DiscuzX/blob/ccf55748e13c1a5497c08d6eb7b7f29b65a6d2a7/upload/source/function/function_member.php) 中的 `checkemail`。实现位于 `interpret_discuz` 和 `community`。

### MyBB

从当前页面的隐藏字段或 JavaScript 变量取得 `my_post_key`。JavaScript 使用 Esprima 读取语法树，页面脚本不会执行。向 `GET xmlhttp.php?action=email_availability` 提交邮箱和当前会话字段。

明确的邮箱重复提示表示发现记录。JSON 字符串 `"true"` 表示字段允许继续填写；MyBB 可以配置多个账户使用同一邮箱，因此此结果的 `registration_status` 保持 `unknown`，并单独保存 `field_available=true`。

协议依据为 MyBB `3b3b3255ae7087c310d739a09fa41587b74d5ec8` 的 [xmlhttp.php](https://github.com/mybb/mybb/blob/3b3b3255ae7087c310d739a09fa41587b74d5ec8/xmlhttp.php) 与 [UserDataHandler.verify_email](https://github.com/mybb/mybb/blob/3b3b3255ae7087c310d739a09fa41587b74d5ec8/inc/datahandlers/user.php)。实现位于 `mybb_token`、`interpret_mybb` 和 `community`。

### Discourse

读取社区页面后调用 `GET u/check_email.json`。如果页面提供预加载 `siteSettings`，同时保存 `hide_email_address_taken` 的实际值。只有明确读取到此设置为 `false` 时，成功响应才能解释为未发现占用记录。设置隐藏或页面未公开此设置时，`{"success":"OK"}` 保留为未知。明确的邮箱已占用错误单独识别。

协议依据为 Discourse `ae65e05533380219967c9e49bdb1f5868b177910` 的 [UsersController.check_email](https://github.com/discourse/discourse/blob/ae65e05533380219967c9e49bdb1f5868b177910/app/controllers/users_controller.rb)。实现位于 `discourse_settings`、`interpret_discourse` 和 `community`。该字段接口在隐藏邮箱状态时也会返回成功，因此 HTTP 200 与成功 JSON 均不足以证明邮箱没有注册。

## 实测和复核

2026-11-11 使用两个获授权的自有邮箱，对上述 73 个目标分别保存检查记录。每项记录包含 HTTP 状态、时间、原始响应、内容校验值、证据引用和观察结果。公开快照位于 `research_core/data/email-verification.json`，真实资料位于 Git 忽略目录 `.runtime/email100-checks/`。

Discuz 社区取得字段通过、域名限制与访问限制等响应；MyBB 社区取得字段可填写及访问限制等响应；Discourse 社区的成功响应保持注册状态未知。样本没有提供独立确认的注册真值，准确率尚未建立；阳性重复邮箱和部分限流分支仍缺少真实样本覆盖。

`tests/test_email_communities.py` 读取真实记录，重新解释响应，检查来源地址、请求方法、协议参数、内容校验值和引用关系。测试数据通过环境变量 `KUDO_CAPTURED_EMAIL_COMMUNITIES` 指定。

```bash
uv run python scripts/probe_email_catalog.py --samples private/email-samples.json --data-dir .runtime/email-community-checks
```

该命令默认检查目录中的 73 个社区；`--sites` 可以指定任意已注册邮箱目标，`--resume` 复用当前目录中已完成的检查。未完成的网络请求会保存实际原因。程序、配置或协议格式错误终止测试并保留执行记录。
