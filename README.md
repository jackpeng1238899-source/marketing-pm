# marketing-pm

自动化任务跟进系统:从飞书多维表格(Bitable)读取数据,生成 Markdown 周报,
并通过 GitHub Actions 定时执行。目前包含两套独立的报告:

1. **市场部任务跟进周报**(`feishu_task_report.py`)—— 汇总市场部各项目
   表里的任务进度,标记超期未更新的任务
2. **CRM 客户拜访跟进周报**(`feishu_crm_visit_report.py`)—— 按客户聚合
   拜访记录,标记超过 N 天没有拜访的客户

## 目录结构

```
.
├── .github/workflows/weekly-task-report.yml       # 任务跟进定时任务
├── .github/workflows/weekly-crm-visit-report.yml  # CRM 拜访跟进定时任务
├── scripts/feishu_common.py                       # 飞书 API 公共辅助函数
├── scripts/feishu_task_report.py                  # 任务跟进报告脚本
├── scripts/feishu_crm_visit_report.py             # CRM 拜访跟进报告脚本
├── reports/                                       # 生成的周报(按日期归档 + latest)
└── requirements.txt
```

## 报告一:市场部任务跟进周报

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

> 注意:飞书里"同一个空间/文件夹下的多个文档"并不一定是"同一个 Base 里的
> 多张表"——如果它们的网址 `/base/` 后面的 App Token 不一样,说明是各自独立
> 的文档,需要分别配置、分别授权。

## 报告二:CRM 客户拜访跟进周报

脚本会自动在 `FEISHU_BITABLE_APP_TOKEN`(此报告读取 `FEISHU_CRM_APP_TOKEN`,
见下方 Secrets 说明)指定的 CRM Base 里,找到名字包含"拜访"的数据表,按
**客户姓名**分组,取每个客户拜访日期最新的一条记录,标记超过 N 天没有拜访
记录的客户。

| 用途         | 匹配关键词           |
| ------------ | --------------------- |
| 客户姓名     | 客户姓名 / 客户       |
| 拜访人       | 拜访人                |
| 拜访日期     | 拜访日期 / 拜访时间   |
| 拜访结果     | 拜访结果              |
| 下次跟进事项 | 下次跟进事项 / 下次跟进 |

## 配置 GitHub Secrets

在仓库 `Settings > Secrets and variables > Actions` 中添加以下 Secrets
(在飞书开放平台创建自建应用,并为其开通多维表格读权限后获取):

| Secret 名称                | 说明                                              |
| --------------------------- | ------------------------------------------------- |
| `FEISHU_APP_ID`              | 应用 App ID(两套报告共用)                        |
| `FEISHU_APP_SECRET`          | 应用 App Secret(两套报告共用)                    |
| `FEISHU_BITABLE_APP_TOKEN`   | 市场部任务跟进用的 Base App Token                 |
| `FEISHU_TABLE_IDS`（可选）   | 逗号分隔的 table_id 列表;不填则自动读取该 Base 下所有表 |
| `FEISHU_CRM_APP_TOKEN`       | CRM 拜访跟进用的 Base App Token(跟上面那个不是同一个 Base) |

可选:在 `Settings > Secrets and variables > Actions > Variables` 中添加
`STALE_DAYS_THRESHOLD`(任务跟进用,默认 7)、`CRM_STALE_DAYS_THRESHOLD`
(CRM 拜访跟进用,默认 30)。

## 本地运行

```bash
pip install -r requirements.txt

export FEISHU_APP_ID=xxx
export FEISHU_APP_SECRET=xxx

# 任务跟进周报
export FEISHU_BITABLE_APP_TOKEN=xxx
python scripts/feishu_task_report.py --stale-days 7

# CRM 拜访跟进周报
export FEISHU_BITABLE_APP_TOKEN=yyy   # 换成 CRM 的 App Token
python scripts/feishu_crm_visit_report.py --stale-days 30
```

生成的报告会写入 `reports/` 目录(按日期归档 + latest 文件)。

## 定时任务

两个 workflow 都是每周一 09:00(北京时间)自动运行,并将生成的报告提交回
`reports/` 目录,也都支持在 Actions 页面手动触发(`workflow_dispatch`)。

> 注意:GitHub Actions 的 `schedule` 触发器只在仓库默认分支上生效,请确保
> workflow 已合并到默认分支后再等待定时触发,或先用 `workflow_dispatch`
> 手动验证。
