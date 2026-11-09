# 第三方来源

## 直接复用

| 项目 | 提交 | 选取内容 |
| --- | --- | --- |
| Sherlock | `c588d7c8b476df9f9c84912a05d1430fb8cdcdae` | 站点规则、`interpolate_string`、页面存在性判断 |
| Maigret | `21577cf7730ddaad0f17d7797e6dbf4642411b50` | 站点与引擎合并、URL 模板识别、用户名检查、页面正反证据和错误标记、站点规则 |

Sherlock 版权所有 © 2019 Sherlock Project。Maigret 版权所有 © 2020–2026 Soxoj。两者采用 MIT，完整声明随安装包保存在 `kudo_agent/upstream/licenses/`。

复用代码已适配统一的数据模型和 HTTP 执行器；数据快照保留指定提交的原文。详细原始文件位置和改动说明见 `kudo_agent/upstream/PROVENANCE.json`。

## 独立实现

Social Analyzer 的多信号核验、Holehe 的按站点检查账户状态与邮箱字段、PhoneInfoga 的号码资料与查询建议、GhostTrack 的本地号码处理及 IP 查询为功能设计参考。对应实现依据功能规格和公开接口独立编写。

邮箱适配器与验证摘要由本项目编写。Holehe 功能研究固定于提交 `14da70f588538936b20d238783c5e28a0772a2b3`；其 GPL 代码及规则数据没有进入发布包。Gitee、腾讯云、博客园和京东的请求依据当前官方客户端的接口声明独立实现。具体接口依据和测试范围见 [邮箱站点](docs/email-providers.md)。

PhoneInfoga 的功能研究基于提交 `55807b05b753dab7bc6fe67403ca385668dda179`，涉及 `lib/remote/local_scanner.go` 与 `lib/remote/googlesearch_scanner.go`。GhostTrack 的功能研究基于提交 `a5cb8ad4c08acd803f166fb067b7dac724d6cb3d`，涉及 `GhostTR.py` 的 `phoneGW()`。本项目使用 phonenumbers 独立实现号码格式、号段资料和页面提及匹配，并独立生成百度、Google 与 Bing 搜索建议。

## 依赖

`socid-extractor` 采用 MIT，`phonenumbers` 采用 Apache-2.0。其余依赖及准确版本记录于 `uv.lock`，各依赖保留自身许可。项目安装包通过依赖声明安装这些组件。
