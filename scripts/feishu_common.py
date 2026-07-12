"""飞书开放平台 Bitable API 的公共辅助函数,供各个报告脚本复用。"""

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

FEISHU_HOST = "https://open.feishu.cn"
TOKEN_URL = f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
TABLES_URL_TMPL = f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{{app_token}}/tables"
RECORDS_URL_TMPL = f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{{app_token}}/tables/{{table_id}}/records/search"

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


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


def find_field(fields: dict[str, Any], keywords: list[str]) -> Any:
    """按关键词子串匹配字段名(飞书各表字段名不完全一致时用来兜底查找)。"""
    for key, value in fields.items():
        for kw in keywords:
            if kw in key:
                return value
    return None
