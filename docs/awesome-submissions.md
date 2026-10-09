# Awesome 列表投稿

2026-10-10（北京时间），参考 dtflow 已有的 GitHub 列表投稿方式，提交以下 3 个 PR。
介绍聚焦 Linux 服务器上的 Textual TUI：通过 SSH 管理 mihomo 订阅、节点和日志。
PR 正文附安装命令、英文 TUI 指南及演示截图，并说明界面目前使用中文标签。

## 已提交

| 列表 / PR | 分类 | 发布时间（UTC） | 状态 |
| --- | --- | --- | --- |
| [Textualize/transcendent-textual #46](https://github.com/Textualize/transcendent-textual/pull/46) | Applications built with Textual | 2026-10-09 17:57:46 | OPEN，待维护者审核 |
| [oleksis/awesome-textualize-projects #16](https://github.com/oleksis/awesome-textualize-projects/pull/16) | Third Party Applications | 2026-10-09 17:57:53 | OPEN，待维护者审核 |
| [toolleeo/awesome-cli-apps-in-a-csv #480](https://github.com/toolleeo/awesome-cli-apps-in-a-csv/pull/480) | networking | 2026-10-09 17:58:02 | OPEN，待维护者审核 |

每个 PR 仅新增一条记录。前两处更新 README；CLI/TUI CSV 列表按
[贡献规则](https://github.com/toolleeo/awesome-cli-apps-in-a-csv/blob/master/CONTRIBUTING.md)
仅更新 `data/apps.csv`，由维护者生成 README。
发布后已回读正文、文件差异、提交和分支，确认与准备的内容一致。
提交不代表已收录，也尚未验证带来访问量或 Stars。

## 验证

- GitHub 项目、英文 TUI 指南、截图及官方 PyPI JSON 均返回 HTTP 200；发布包为 0.1.4。
- 投稿前，在三个列表的已有条目及当时的 PR/issue 中未发现 mihomo-py；三处 `git diff --check` 通过。
- CSV 保留全部原记录，新增一条合法的五列 `networking` 记录。
- 独立 `/qa` 复核通过；投稿内容、分类、排序和格式符合各列表规则。
- `npx --yes awesome-lint`（2.3.0）本地退出码 0，仅有上游 README 第 60 行已有的拼写 warning。
  [远端 lint 工作流](https://github.com/oleksis/awesome-textualize-projects/actions/runs/37969969751)
  当前为 `action_required`，需要维护者批准运行；未把本地通过记为远端 CI 通过。

功能介绍使用现有测试验证，运行真实 Textual 界面及本地 mihomo 内核，结果为
`2 passed in 9.11s`：

```bash
python -m pytest -q \
  tests/test_tui.py::test_empty_ui_keyboard_and_modal_cancel \
  tests/test_tui.py::test_nodes_search_switch_delay_settings_and_logs
```

## 本轮跳过

投稿时项目仓库创建于 2026-10-09，Stars 为 0，未声明项目开源许可证。
以下列表有不满足的明确门槛，未提交：

| 列表 | 当前规则 | 后续条件 |
| --- | --- | --- |
| [rothgar/awesome-tuis](https://github.com/rothgar/awesome-tuis/blob/main/.github/pull_request_template.md) | 仓库至少存在 6 个月 | 满足年龄要求后重新核对规则 |
| [agarrharr/awesome-cli-apps](https://github.com/agarrharr/awesome-cli-apps/blob/master/contributing.md) | 超过 3 个月、超过 20 Stars、有开源许可证；拒绝 AI 生成的 PR | 满足门槛后由维护者自行撰写投稿理由 |
| [alebcay/awesome-shell](https://github.com/alebcay/awesome-shell/blob/master/CONTRIBUTING.md) | 至少 50 Stars | 满足 Stars 门槛后重新核对规则 |

后续在原 PR 中处理维护者反馈，并更新本页状态；不要重复投稿。
