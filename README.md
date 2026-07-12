# marketing-pm

自动化任务跟进系统:从飞书多维表格(Bitable)读取任务数据,生成 Markdown
周报,并通过 GitHub Actions 定时执行。

## 目录结构

```
.
├── .github/workflows/weekly-task-report.yml  # 定时任务(每周一 09:00 北京时间)
├── scripts/feishu_task_report.py             # 报告生成脚本
├── reports/                                  # 生成的周报(按日期归档 + latest.md)
└── requirements.txt
```

## 飞书表格字段要求

脚本会读取 `FEISHU_BITABLE_APP_TOKEN` 指定的 Base 下的数据表,支持**一次读取
多张表并汇总**。各表字段名不要求完全一致,脚本按关键词自动识别:

| 用途     | 匹配关键词(字段名包含其一即可)                     | 缺失时的兜底逻辑                         |
| -------- | --------------------------------------------------- | ------------------------------------------ |
| 任务名称 | 任务名称 / 任务 / 标题                               | 该记录跳过(视为非任务行)                 |
| 负责人   | 负责人                                                | 显示"(未分配)"                           |
| 进度     | 进度 / 状态                                           | 用"实际完成时间/实际开始时间"推断已完成/进行中/未开始 |
| 更新日期 | 更新日期 / 更新时间                                   | 依次尝试实际完成时间→实际开始时间→计划结束时间 |
| 备注     | 备注 / 说明                                           | 留空                                       |

默认会自动读取该 Base 下**所有数据表**;如果只想读取指定的几张表,设置
`FEISHU_TABLE_IDS`(逗号分隔的 table_id 列表)即可。

## 配置 GitHub Secrets

在仓库 `Settings > Secrets and variables > Actions` 中添加以下 Secrets
(在飞书开放平台创建自建应用,并为其开通多维表格读权限后获取):

| Secret 名称                | 说明                                              |
| --------------------------- | ------------------------------------------------- |
| `FEISHU_APP_ID`              | 应用 App ID                                       |
| `FEISHU_APP_SECRET`          | 应用 App Secret                                   |
| `FEISHU_BITABLE_APP_TOKEN`   | 多维表格 Base 的 App Token(表格 URL 中获取)       |
| `FEISHU_TABLE_IDS`（可选）   | 逗号分隔的 table_id 列表;不填则自动读取该 Base 下所有表 |

可选:在 `Settings > Secrets and variables > Actions > Variables` 中添加
`STALE_DAYS_THRESHOLD`(整数,默认 7),用于配置多少天未更新算作需要关注。

## 本地运行

```bash
pip install -r requirements.txt

export FEISHU_APP_ID=xxx
export FEISHU_APP_SECRET=xxx
export FEISHU_BITABLE_APP_TOKEN=xxx
export FEISHU_TABLE_IDS=xxx,yyy   # 可选,不填则读取该 Base 下所有表

python scripts/feishu_task_report.py --stale-days 7
```

生成的报告会写入 `reports/<YYYY-MM-DD>.md` 和 `reports/latest.md`。

## 定时任务

`.github/workflows/weekly-task-report.yml` 每周一 09:00(北京时间)自动运行
脚本,并将生成的报告提交回 `reports/` 目录。也可以在 Actions 页面手动触发
(`workflow_dispatch`)。

> 注意:GitHub Actions 的 `schedule` 触发器只在仓库默认分支上生效,请确保
> 该 workflow 已合并到默认分支后再等待定时触发,或先用 `workflow_dispatch`
> 手动验证。
