"""StaffDeck 后端模块：HTTP GET 工具请求构建器，把工具参数安全合并到查询字符串。

主要入口：prepare_get_request。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def prepare_get_request(url: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    clean_params = {str(key): value for key, value in (params or {}).items() if value is not None}
    if not clean_params:
        return url, {}

    parsed = urlsplit(url)
    if not parsed.query:
        return url, {"params": clean_params}

    merged_params: dict[str, Any] = dict(parse_qsl(parsed.query, keep_blank_values=True))
    merged_params.update(clean_params)
    merged_url = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(merged_params, doseq=True),
            parsed.fragment,
        )
    )
    return merged_url, {}
