#!/usr/bin/env python3
"""从飞书多维表格(Bitable)读取任务数据,生成任务跟进 Markdown 周报。

支持读取一个 Base 下的多张数据表,自动汇总。每张表字段名不要求完全一致,
通过关键词匹配自动识别"任务名称/负责人/进度/更新日期/备注"等列,缺失时用
合理的兜底逻辑(比如用实际完成/开始时间推断进度状态)。

所需环境变量:
    FEISHU_APP_ID              飞书应用 App ID
    FEISHU_APP_SECRET          飞书应用 App Secret
    FEISHU_BITABLE_APP_TOKEN   多维表格 Base 的 App Token

可选环境变量 / 参数:
    FEISHU_TABLE_IDS                      指定要读取的表(逗号分隔的 table_id),
                                           不填则自动读取该 Base 下所有数据表
    STALE_DAYS_THRESHOLD / --stale-days   超过多少天未更新视为需要关注,默认 7 天
"""

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

FEISHU_HOST = "https://open.feishu.cn"
TOKEN_URL = f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
TABLES_URL_TMPL = f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{{app_token}}/tables"
RECORDS_URL_TMPL = f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{{app_token}}/tables/{{table_id}}/records/search"

BEIJING_TZ = ZoneInfo("Asia/Shanghai")

NAME_KEYWORDS = ["任务名称", "任务", "标题"]
OWNER_KEYWORDS = ["负责人"]
PROGRESS_KEYWORDS = ["进度", "状态"]
UPDATED_KEYWORDS = ["更新日期", "更新时间"]
FALLBACK_DATE_KEYWORDS = ["实际完成时间", "实际开始时间", "计划结束时间", "计划完成时间"]
NOTES_KEYWORDS = ["备注", "说明"]
DONE_KEYWORDS = ["实际完成时间", "完成时间"]
STARTED_KEYWORDS = ["实际开始时间", "开始时间"]

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"


@dataclass
class Task:
    source_table: str
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


def request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """统一发请求并在失败时把响应体带出来,方便定位飞书返回的具体错误。"""
    resp = requests.request(method, url, timeout=10, **kwargs)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code} 调用 {url} 失败: {resp.text}")
    return resp.json()


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    data = request_json(
        "POST",
        TOKEN_URL,
        json={"app_id": app_id, "app_secret": app_secret},
    )
    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")
    return data["tenant_access_token"]


def fetch_all_tables(token: str, app_token: str) -> list[dict[str, Any]]:
    url = TABLES_URL_TMPL.format(app_token=app_token)
    headers = {"Authorization": f"Bearer {token}"}
    tables: list[dict[str, Any]] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        data = request_json("GET", url, headers=headers, params=params)
        if data.get("code") != 0:
            raise RuntimeError(f"获取数据表列表失败: {data}")
        payload = data["data"]
        tables.extend(payload.get("items", []))
        if payload.get("has_more"):
            page_token = payload.get("page_token")
        else:
            break
    return tables


def fetch_all_records(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    url = RECORDS_URL_TMPL.format(app_token=app_token, table_id=table_id)
    headers = {"Authorization": f"Bearer {token}"}
    records: list[dict[str, Any]] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        data = request_json("POST", url, headers=headers, params=params, json={})
        if data.get("code") != 0:
            raise RuntimeError(f"读取多维表格记录失败(table_id={table_id}): {data}")
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
        if "text" in value:
            return str(value["text"])
        if "name" in value:
            return str(value["name"])
        if "value" in value:
            return stringify_field(value["value"])
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            parts.append(stringify_field(item) if isinstance(item, (dict, list)) else str(item))
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


def find_field(fields: dict[str, Any], keywords: list[str]) -> Any:
    """按关键词子串匹配字段名(飞书各表字段名不完全一致时用来兜底查找)。"""
    for key, value in fields.items():
        for kw in keywords:
            if kw in key:
                return value
    return None


def derive_progress(fields: dict[str, Any]) -> str:
    """没有显式"进度/状态"字段时,用实际开始/完成时间推断进度状态。"""
    explicit = find_field(fields, PROGRESS_KEYWORDS)
    text = stringify_field(explicit)
    if text:
        return text
    if parse_date_field(find_field(fields, DONE_KEYWORDS)):
        return "已完成"
    if parse_date_field(find_field(fields, STARTED_KEYWORDS)):
        return "进行中"
    return "未开始"


def derive_updated_at(fields: dict[str, Any]) -> Optional[datetime]:
    date = parse_date_field(find_field(fields, UPDATED_KEYWORDS))
    if date:
        return date
    for kw in FALLBACK_DATE_KEYWORDS:
        date = parse_date_field(find_field(fields, [kw]))
        if date:
            return date
    return None


def build_tasks(records: list[dict[str, Any]], source_table: str) -> list[Task]:
    tasks = []
    for record in records:
        fields = record.get("fields", {})
        name = stringify_field(find_field(fields, NAME_KEYWORDS))
        if not name:
            continue
        tasks.append(
            Task(
                source_table=source_table,
                name=name,
                owner=stringify_field(find_field(fields, OWNER_KEYWORDS)) or "(未分配)",
                progress=derive_progress(fields),
                updated_at=derive_updated_at(fields),
                notes=stringify_field(find_field(fields, NOTES_KEYWORDS)),
            )
        )
    return tasks


def render_report(tasks: list[Task], stale_days: int, table_count: int) -> str:
    now = datetime.now(BEIJING_TZ)
    lines: list[str] = []

    lines.append("# 任务跟进周报")
    lines.append("")
    lines.append(f"**生成时间**: {now.strftime('%Y-%m-%d %H:%M')} (Asia/Shanghai)")
    lines.append("")

    progress_values = [
        v for t in tasks if (v := parse_progress_ratio(t.progress)) is not None
    ]
    stale_tasks = [
        t
        for t in tasks
        if t.days_since_update is not None and t.days_since_update > stale_days and "完成" not in t.progress
    ]
    no_update_tasks = [t for t in tasks if t.updated_at is None]

    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 数据来源表数: {table_count}")
    lines.append(f"- 任务总数: {len(tasks)}")
    if len(progress_values) > len(tasks) / 2 and progress_values:
        avg_progress = f"{sum(progress_values) / len(progress_values):.0f}%"
        lines.append(f"- 平均进度: {avg_progress}")
    else:
        status_counts = Counter(t.progress for t in tasks)
        breakdown = " | ".join(f"{status}: {count}" for status, count in status_counts.most_common())
        lines.append(f"- 状态分布: {breakdown or 'N/A'}")
    lines.append(f"- 超过 {stale_days} 天未更新: {len(stale_tasks)}")
    if no_update_tasks:
        lines.append(f"- 缺少更新/完成日期: {len(no_update_tasks)}")
    lines.append("")

    lines.append("| 来源表 | 任务名称 | 负责人 | 进度 | 更新日期 | 备注 |")
    lines.append("|---|---|---|---|---|---|")
    if tasks:
        for t in sorted(tasks, key=lambda x: (x.source_table, x.name)):
            updated_str = t.updated_at.strftime("%Y-%m-%d") if t.updated_at else "-"
            lines.append(
                f"| {t.source_table} | {t.name} | {t.owner} | {t.progress} | {updated_str} | {t.notes or '-'} |"
            )
    else:
        lines.append("| - | - | - | - | - | - |")
    lines.append("")

    lines.append("## ⚠️ 需要关注的事项")
    lines.append("")
    if stale_tasks:
        lines.append(f"以下任务超过 {stale_days} 天未更新,请及时跟进:")
        lines.append("")
        for t in sorted(stale_tasks, key=lambda x: x.days_since_update, reverse=True):
            lines.append(
                f"- **[{t.source_table}] {t.name}**(负责人: {t.owner})"
                f"— 已 {t.days_since_update} 天未更新,当前进度: {t.progress}"
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
    table_ids_env = os.environ.get("FEISHU_TABLE_IDS")

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

    if table_ids_env:
        wanted_ids = {t.strip() for t in table_ids_env.split(",") if t.strip()}
        selected_tables = [t for t in all_tables if t.get("table_id") in wanted_ids]
    else:
        selected_tables = all_tables

    tasks: list[Task] = []
    for table in selected_tables:
        table_id = table.get("table_id")
        table_name = table.get("name") or table_id
        records = fetch_all_records(token, app_token, table_id)
        tasks.extend(build_tasks(records, table_name))

    report = render_report(tasks, args.stale_days, len(selected_tables))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    (REPORT_DIR / f"{today}.md").write_text(report, encoding="utf-8")
    (REPORT_DIR / "latest.md").write_text(report, encoding="utf-8")

    print(f"报告已生成: {REPORT_DIR / f'{today}.md'}(来源表 {len(selected_tables)} 张,任务 {len(tasks)} 条)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
