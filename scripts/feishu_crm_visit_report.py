#!/usr/bin/env python3
"""从飞书 CRM 多维表格读取拜访记录,按客户聚合"最近一次拜访",生成
Markdown 跟进周报:哪些客户超过 N 天没有拜访记录,需要及时安排跟进。

所需环境变量:
    FEISHU_APP_ID              飞书应用 App ID
    FEISHU_APP_SECRET          飞书应用 App Secret
    FEISHU_BITABLE_APP_TOKEN   CRM 多维表格 Base 的 App Token

可选环境变量 / 参数:
    STALE_DAYS_THRESHOLD / --stale-days   超过多少天未拜访视为需要关注,默认 30 天
"""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from feishu_common import (
    BEIJING_TZ,
    fetch_all_records,
    fetch_all_tables,
    find_field,
    get_tenant_access_token,
    parse_date_field,
    stringify_field,
)

VISIT_TABLE_NAME_KEYWORDS = ["拜访"]
CUSTOMER_KEYWORDS = ["客户姓名", "客户"]
VISITOR_KEYWORDS = ["拜访人"]
VISIT_DATE_KEYWORDS = ["拜访日期", "拜访时间"]
RESULT_KEYWORDS = ["拜访结果"]
NEXT_STEP_KEYWORDS = ["下次跟进事项", "下次跟进"]

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"


@dataclass
class CustomerVisit:
    customer: str
    visitor: str
    last_visit_at: Optional[datetime]
    last_result: str
    next_step: str
    visit_count: int

    @property
    def days_since_visit(self) -> Optional[int]:
        if self.last_visit_at is None:
            return None
        now = datetime.now(BEIJING_TZ)
        return (now.date() - self.last_visit_at.astimezone(BEIJING_TZ).date()).days


def find_visit_table(all_tables: list[dict[str, Any]]) -> dict[str, Any]:
    for table in all_tables:
        name = table.get("name", "")
        if any(kw in name for kw in VISIT_TABLE_NAME_KEYWORDS):
            return table
    raise RuntimeError(
        f"没有找到名字包含{VISIT_TABLE_NAME_KEYWORDS}的拜访记录表,"
        f"该 Base 下的表有: {[t.get('name') for t in all_tables]}"
    )


def aggregate_by_customer(records: list[dict[str, Any]]) -> list[CustomerVisit]:
    """按客户姓名分组,取每个客户拜访日期最新的一条记录作为"最近拜访"。"""
    latest_by_customer: dict[str, CustomerVisit] = {}
    visit_counts: dict[str, int] = {}

    for record in records:
        fields = record.get("fields", {})
        customer = stringify_field(find_field(fields, CUSTOMER_KEYWORDS))
        if not customer:
            continue
        visit_counts[customer] = visit_counts.get(customer, 0) + 1

        visit_at = parse_date_field(find_field(fields, VISIT_DATE_KEYWORDS))
        existing = latest_by_customer.get(customer)
        if existing is not None and existing.last_visit_at is not None:
            if visit_at is None or visit_at <= existing.last_visit_at:
                continue

        latest_by_customer[customer] = CustomerVisit(
            customer=customer,
            visitor=stringify_field(find_field(fields, VISITOR_KEYWORDS)) or "(未分配)",
            last_visit_at=visit_at,
            last_result=stringify_field(find_field(fields, RESULT_KEYWORDS)),
            next_step=stringify_field(find_field(fields, NEXT_STEP_KEYWORDS)),
            visit_count=0,
        )

    for customer, visit in latest_by_customer.items():
        visit.visit_count = visit_counts[customer]

    return list(latest_by_customer.values())


def render_report(visits: list[CustomerVisit], stale_days: int) -> str:
    now = datetime.now(BEIJING_TZ)
    lines: list[str] = []

    lines.append("# CRM 客户拜访跟进周报")
    lines.append("")
    lines.append(f"**生成时间**: {now.strftime('%Y-%m-%d %H:%M')} (Asia/Shanghai)")
    lines.append("")

    stale_visits = [
        v for v in visits if v.days_since_visit is not None and v.days_since_visit > stale_days
    ]
    no_date_visits = [v for v in visits if v.last_visit_at is None]

    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 有拜访记录的客户数: {len(visits)}")
    lines.append(f"- 超过 {stale_days} 天未拜访: {len(stale_visits)}")
    if no_date_visits:
        lines.append(f"- 缺少拜访日期: {len(no_date_visits)}")
    lines.append("")

    lines.append("| 客户姓名 | 拜访人 | 最近拜访日期 | 拜访次数 | 最近拜访结果 | 下次跟进事项 |")
    lines.append("|---|---|---|---|---|---|")
    if visits:
        for v in sorted(visits, key=lambda x: x.customer):
            visit_str = v.last_visit_at.strftime("%Y-%m-%d") if v.last_visit_at else "-"
            lines.append(
                f"| {v.customer} | {v.visitor} | {visit_str} | {v.visit_count} | "
                f"{v.last_result or '-'} | {v.next_step or '-'} |"
            )
    else:
        lines.append("| - | - | - | - | - | - |")
    lines.append("")

    lines.append("## ⚠️ 需要关注的事项")
    lines.append("")
    if stale_visits:
        lines.append(f"以下客户超过 {stale_days} 天没有拜访记录,请及时安排跟进:")
        lines.append("")
        for v in sorted(stale_visits, key=lambda x: x.days_since_visit, reverse=True):
            lines.append(
                f"- **{v.customer}**(拜访人: {v.visitor})"
                f"— 已 {v.days_since_visit} 天未拜访,上次结果: {v.last_result or '无记录'}"
            )
    else:
        lines.append("暂无需要关注的事项 🎉")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成飞书 CRM 客户拜访跟进周报")
    parser.add_argument(
        "--stale-days",
        type=int,
        default=int(os.environ.get("STALE_DAYS_THRESHOLD") or "30"),
        help="超过多少天未拜访的客户将被标记提醒(默认 30 天,也可用环境变量 STALE_DAYS_THRESHOLD 配置)",
    )
    args = parser.parse_args()

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    app_token = os.environ.get("FEISHU_BITABLE_APP_TOKEN")

    missing = [
        name
        for name, val in [
            ("FEISHU_APP_ID", app_id),
            ("FEISHU_APP_SECRET", app_secret),
            ("FEISHU_BITABLE_APP_TOKEN", app_token),
        ]
        if not val
    ]
    if missing:
        print(f"缺少必要的环境变量: {', '.join(missing)}", file=sys.stderr)
        return 1

    token = get_tenant_access_token(app_id, app_secret)
    all_tables = fetch_all_tables(token, app_token)
    visit_table = find_visit_table(all_tables)
    records = fetch_all_records(token, app_token, visit_table["table_id"])
    visits = aggregate_by_customer(records)

    report = render_report(visits, args.stale_days)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    (REPORT_DIR / f"crm-visit-{today}.md").write_text(report, encoding="utf-8")
    (REPORT_DIR / "crm-visit-latest.md").write_text(report, encoding="utf-8")

    print(
        f"报告已生成: {REPORT_DIR / f'crm-visit-{today}.md'}"
        f"(客户 {len(visits)} 位,拜访记录 {len(records)} 条)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
