#!/usr/bin/env python3
"""从飞书多维表格(Bitable)读取任务数据,生成任务跟进 Markdown 周报。

所需环境变量:
    FEISHU_APP_ID              飞书应用 App ID
    FEISHU_APP_SECRET          飞书应用 App Secret
    FEISHU_BITABLE_APP_TOKEN   多维表格 App Token
    FEISHU_TABLE_ID            数据表 Table ID

可选环境变量 / 参数:
    STALE_DAYS_THRESHOLD / --stale-days   超过多少天未更新视为需要关注,默认 7 天
"""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

FEISHU_HOST = "https://open.feishu.cn"
TOKEN_URL = f"{FEISHU_HOST}/open-api/auth/v3/tenant_access_token/internal"
RECORDS_URL_TMPL = f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{{app_token}}/tables/{{table_id}}/records"

BEIJING_TZ = ZoneInfo("Asia/Shanghai")

FIELD_NAME = "任务名称"
FIELD_OWNER = "负责人"
FIELD_PROGRESS = "进度"
FIELD_UPDATED_AT = "更新日期"
FIELD_NOTES = "备注"

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"


@dataclass
class Task:
    name: str
    owner: str
    progress: str
    updated_at: Optional[datetime]
    notes: str

    @property
    def days_since_update(self) -> Optional[int]:
        if self.updated_at is None:
            return None
        now = datetime.now(BEIJING_TZ)
        return (now.date() - self.updated_at.astimezone(BEIJING_TZ).date()).days


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    resp = requests.post(
        TOKEN_URL,
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")
    return data["tenant_access_token"]


def fetch_all_records(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    url = RECORDS_URL_TMPL.format(app_token=app_token, table_id=table_id)
    headers = {"Authorization": f"Bearer {token}"}
    records: list[dict[str, Any]] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"读取多维表格记录失败: {data}")
        payload = data["data"]
        records.extend(payload.get("items", []))
        if payload.get("has_more"):
            page_token = payload.get("page_token")
        else:
            break
    return records


def stringify_field(value: Any) -> str:
    """将飞书字段值(可能是字符串/数字/富文本数组/人员数组等)统一转为可读文本。"""
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or item))
            else:
                parts.append(str(item))
        return ", ".join(p for p in parts if p)
    return str(value)


def parse_date_field(value: Any) -> Optional[datetime]:
    """解析飞书日期字段,支持毫秒时间戳或常见日期字符串格式。"""
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=BEIJING_TZ)
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=BEIJING_TZ)
            except ValueError:
                continue
    return None


def parse_progress_ratio(raw: str) -> Optional[float]:
    """尽量从进度字段中解析出数值(如 "60%" / 60 / "60"),用于计算平均进度。"""
    digits = "".join(ch for ch in raw if ch.isdigit() or ch == ".")
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def build_tasks(records: list[dict[str, Any]]) -> list[Task]:
    tasks = []
    for record in records:
        fields = record.get("fields", {})
        tasks.append(
            Task(
                name=stringify_field(fields.get(FIELD_NAME)) or "(未命名任务)",
                owner=stringify_field(fields.get(FIELD_OWNER)) or "(未分配)",
                progress=stringify_field(fields.get(FIELD_PROGRESS)) or "-",
                updated_at=parse_date_field(fields.get(FIELD_UPDATED_AT)),
                notes=stringify_field(fields.get(FIELD_NOTES)),
            )
        )
    return tasks


def render_report(tasks: list[Task], stale_days: int) -> str:
    now = datetime.now(BEIJING_TZ)
    lines: list[str] = []

    lines.append("# 任务跟进周报")
    lines.append("")
    lines.append(f"**生成时间**: {now.strftime('%Y-%m-%d %H:%M')} (Asia/Shanghai)")
    lines.append("")

    progress_values = [
        v for t in tasks if (v := parse_progress_ratio(t.progress)) is not None
    ]
    avg_progress = f"{sum(progress_values) / len(progress_values):.0f}%" if progress_values else "N/A"
    stale_tasks = [t for t in tasks if t.days_since_update is not None and t.days_since_update > stale_days]
    no_update_tasks = [t for t in tasks if t.updated_at is None]

    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 任务总数: {len(tasks)}")
    lines.append(f"- 平均进度: {avg_progress}")
    lines.append(f"- 超过 {stale_days} 天未更新: {len(stale_tasks)}")
    if no_update_tasks:
        lines.append(f"- 缺少更新日期: {len(no_update_tasks)}")
    lines.append("")

    lines.append("| 任务名称 | 负责人 | 进度 | 更新日期 | 备注 |")
    lines.append("|---|---|---|---|---|")
    if tasks:
        for t in tasks:
            updated_str = t.updated_at.strftime("%Y-%m-%d") if t.updated_at else "-"
            lines.append(f"| {t.name} | {t.owner} | {t.progress} | {updated_str} | {t.notes or '-'} |")
    else:
        lines.append("| - | - | - | - | - |")
    lines.append("")

    lines.append("## ⚠️ 需要关注的事项")
    lines.append("")
    if stale_tasks:
        lines.append(f"以下任务超过 {stale_days} 天未更新,请及时跟进:")
        lines.append("")
        for t in sorted(stale_tasks, key=lambda x: x.days_since_update, reverse=True):
            lines.append(
                f"- **{t.name}**(负责人: {t.owner})— 已 {t.days_since_update} 天未更新,当前进度: {t.progress}"
            )
    else:
        lines.append("暂无需要关注的事项 🎉")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成飞书多维表格任务跟进周报")
    parser.add_argument(
        "--stale-days",
        type=int,
        default=int(os.environ.get("STALE_DAYS_THRESHOLD") or "7"),
        help="超过多少天未更新的任务将被标记提醒(默认 7 天,也可用环境变量 STALE_DAYS_THRESHOLD 配置)",
    )
    args = parser.parse_args()

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    app_token = os.environ.get("FEISHU_BITABLE_APP_TOKEN")
    table_id = os.environ.get("FEISHU_TABLE_ID")

    missing = [
        name
        for name, val in [
            ("FEISHU_APP_ID", app_id),
            ("FEISHU_APP_SECRET", app_secret),
            ("FEISHU_BITABLE_APP_TOKEN", app_token),
            ("FEISHU_TABLE_ID", table_id),
        ]
        if not val
    ]
    if missing:
        print(f"缺少必要的环境变量: {', '.join(missing)}", file=sys.stderr)
        return 1

    token = get_tenant_access_token(app_id, app_secret)
    records = fetch_all_records(token, app_token, table_id)
    tasks = build_tasks(records)
    report = render_report(tasks, args.stale_days)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    (REPORT_DIR / f"{today}.md").write_text(report, encoding="utf-8")
    (REPORT_DIR / "latest.md").write_text(report, encoding="utf-8")

    print(f"报告已生成: {REPORT_DIR / f'{today}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
