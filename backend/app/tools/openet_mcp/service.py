"""OpenET tool contracts, deterministic selection, validation, and HTTP access."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from app.tools.openet_mcp.catalog import (
    CATALOG_SOURCE,
    CATALOG_VERSION,
    DATASETS,
    DatasetCategory,
    DatasetSpec,
    all_variables_supported,
    base_variable_name,
)


OPENET_API_BASE_URL = "https://api-pro-openet.terraqt.com/v1"
GEOCODING_API_BASE_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_TIMEOUT_SECONDS = 20.0
GEOCODING_TIMEOUT_SECONDS = 12.0
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class OpenETMCPError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        upstream_code: str | int | None = None,
        dataset: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.upstream_code = upstream_code
        self.dataset = dataset

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"code": self.code, "message": self.message}
        if self.upstream_code is not None:
            result["upstream_code"] = self.upstream_code
        if self.dataset:
            result["dataset"] = self.dataset
        return result


def load_openet_token(env_file: Path | None = None) -> str:
    """Read the token from the backend dotenv file, never from MCP config."""
    selected = env_file or Path(os.environ.get("ULTRARAG_DOTENV", ".env"))
    try:
        value = dotenv_values(selected).get("OPENET_API_TOKEN")
    except OSError:
        return ""
    return str(value or "").strip()


class OpenETClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = OPENET_API_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = load_openet_token() if token is None else token.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def query(self, dataset: str, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._token:
            raise OpenETMCPError(
                "AUTH_MISSING",
                "OPENET_API_TOKEN is not configured in backend/.env.",
                dataset=dataset,
            )
        url = f"{self._base_url}/{dataset}/{endpoint.lstrip('/')}"
        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = client.post(
                    url,
                    headers={"Content-Type": "application/json", "token": self._token},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise OpenETMCPError(
                "UPSTREAM_TIMEOUT", "OpenET request timed out.", dataset=dataset
            ) from exc
        except httpx.HTTPError as exc:
            raise OpenETMCPError(
                "UPSTREAM_ERROR",
                _sanitize_message(str(exc), self._token),
                dataset=dataset,
            ) from exc

        if response.status_code in {401, 403}:
            raise OpenETMCPError(
                "AUTH_REJECTED",
                f"OpenET rejected the configured token (HTTP {response.status_code}).",
                dataset=dataset,
            )
        if not response.is_success:
            raise OpenETMCPError(
                "UPSTREAM_ERROR",
                f"OpenET returned HTTP {response.status_code}.",
                dataset=dataset,
            )
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise OpenETMCPError(
                "RESPONSE_INVALID", "OpenET response is not valid JSON.", dataset=dataset
            ) from exc
        if not isinstance(body, dict):
            raise OpenETMCPError(
                "RESPONSE_INVALID", "OpenET response root is not an object.", dataset=dataset
            )
        if body.get("success") is not True:
            upstream_code = body.get("code")
            message = _sanitize_message(str(body.get("msg") or "OpenET business error."), self._token)
            raise OpenETMCPError(
                _map_upstream_error(upstream_code, message),
                message,
                upstream_code=upstream_code,
                dataset=dataset,
            )
        data = body.get("data")
        if not isinstance(data, dict):
            raise OpenETMCPError(
                "RESPONSE_INVALID", "OpenET response data is not an object.", dataset=dataset
            )
        return data


@dataclass(frozen=True)
class ResolvedLocation:
    query: str
    display_name: str
    longitude: float
    latitude: float

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "display_name": self.display_name,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "source": "OpenStreetMap Nominatim",
            "attribution": "Data (c) OpenStreetMap contributors, ODbL 1.0",
        }


class LocationResolver:
    """Resolve human-readable places while keeping coordinates out of user interactions."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = GEOCODING_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("OPENET_GEOCODING_URL") or GEOCODING_API_BASE_URL
        ).strip()
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def resolve(self, query: str) -> ResolvedLocation:
        normalized = str(query or "").strip()
        if not normalized:
            raise OpenETMCPError("VALIDATION_ERROR", "location must be a non-empty string.")
        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = client.get(
                    self._base_url,
                    params={
                        "q": normalized,
                        "format": "jsonv2",
                        "limit": 5,
                        "accept-language": "zh-CN",
                        "countrycodes": "cn",
                    },
                    headers={"User-Agent": "StaffDeck/0.1 (OpenET MCP location resolver)"},
                )
        except httpx.TimeoutException as exc:
            raise OpenETMCPError(
                "GEOCODING_TIMEOUT", "地点解析服务超时，请稍后重试。"
            ) from exc
        except httpx.HTTPError as exc:
            raise OpenETMCPError(
                "GEOCODING_ERROR", f"地点解析服务不可用：{_sanitize_message(str(exc))}"
            ) from exc
        if not response.is_success:
            raise OpenETMCPError(
                "GEOCODING_ERROR", f"地点解析服务返回 HTTP {response.status_code}。"
            )
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise OpenETMCPError("GEOCODING_ERROR", "地点解析服务返回了无效数据。") from exc
        if not isinstance(body, list):
            raise OpenETMCPError("GEOCODING_ERROR", "地点解析服务返回了无效数据。")

        candidates: list[ResolvedLocation] = []
        for item in body:
            if not isinstance(item, dict):
                continue
            try:
                longitude = float(item.get("lon"))
                latitude = float(item.get("lat"))
            except (TypeError, ValueError):
                continue
            if not 72 <= longitude <= 137 or not 17 <= latitude <= 55:
                continue
            display_name = str(item.get("display_name") or item.get("name") or "").strip()
            if not display_name:
                continue
            candidates.append(
                ResolvedLocation(normalized, display_name, longitude, latitude)
            )
        if not candidates:
            raise OpenETMCPError(
                "LOCATION_NOT_FOUND",
                f"无法识别地点“{normalized}”，请补充城市、区县或省份。",
            )

        distinct: list[ResolvedLocation] = []
        seen: set[tuple[str, float, float]] = set()
        for candidate in candidates:
            key = (
                candidate.display_name,
                round(candidate.longitude, 4),
                round(candidate.latitude, 4),
            )
            if key not in seen:
                seen.add(key)
                distinct.append(candidate)
        if len(distinct) > 1:
            choices = "；".join(item.display_name for item in distinct[:3])
            raise OpenETMCPError(
                "LOCATION_AMBIGUOUS",
                f"地点“{normalized}”存在多个候选（{choices}），请补充省份或城市。",
            )
        return distinct[0]


def _map_upstream_error(code: object, message: str) -> str:
    normalized = str(code or "").upper()
    lower_message = message.lower()
    if any(item in lower_message for item in ("token", "auth", "permission", "unauthorized")):
        return "AUTH_REJECTED"
    if normalized.startswith("E102"):
        return "OUT_OF_SCOPE"
    if normalized.startswith("E103"):
        return "NO_DATA"
    if normalized.startswith("E104") or normalized == "E10105":
        return "VARIABLE_UNAVAILABLE"
    if normalized.startswith("E101"):
        return "VALIDATION_ERROR"
    return "UPSTREAM_ERROR"


def _sanitize_message(message: str, token: str = "") -> str:
    result = message.replace("\r", " ").replace("\n", " ")
    if token:
        result = result.replace(token, "[REDACTED]")
    result = re.sub(
        r'(?i)(token\s*[:=]\s*)[^\s,;}"]+',
        r"\1[REDACTED]",
        result,
    )
    return result[:500]


@dataclass(frozen=True)
class Selection:
    requested_dataset: str
    dataset: DatasetSpec
    reason: str
    alternatives: tuple[str, ...]


class OpenETService:
    def __init__(
        self,
        client: OpenETClient | None = None,
        *,
        now_utc: Callable[[], datetime] | None = None,
        location_resolver: LocationResolver | None = None,
    ) -> None:
        self._client = client or OpenETClient()
        self._now_utc = now_utc or datetime.utcnow
        self._location_resolver = location_resolver or LocationResolver()

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "list_datasets": self._list_datasets,
            "describe_dataset": self._describe_dataset,
            "get_point_forecast": self._get_point_forecast,
            "get_ensemble_forecast": self._get_ensemble_forecast,
            "get_point_history": self._get_point_history,
            "get_multi_point_forecast": self._get_multi_point_forecast,
            "get_area_average_forecast": self._get_area_average_forecast,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            raise OpenETMCPError("VALIDATION_ERROR", f"Unknown OpenET tool: {tool_name}")
        return handler(args)

    def _list_datasets(self, args: dict[str, Any]) -> dict[str, Any]:
        _reject_unknown_arguments(args, {"category"})
        category = args.get("category")
        if category is not None and category not in {"forecast", "ensemble", "history"}:
            raise OpenETMCPError(
                "VALIDATION_ERROR", "category must be forecast, ensemble, or history."
            )
        datasets = [
            item.as_summary()
            for item in DATASETS.values()
            if category is None or item.category == category
        ]
        return {
            "catalog_version": CATALOG_VERSION,
            "catalog_source": CATALOG_SOURCE,
            "datasets": datasets,
        }

    def _describe_dataset(self, args: dict[str, Any]) -> dict[str, Any]:
        _reject_unknown_arguments(args, {"dataset"})
        dataset_key = _required_string(args, "dataset")
        dataset = DATASETS.get(dataset_key)
        if dataset is None:
            raise OpenETMCPError(
                "OUT_OF_SCOPE", f"Dataset is not in the OpenET v1 catalog: {dataset_key}"
            )
        return dataset.as_detail()

    def _get_point_forecast(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "dataset", "selection_goal", "location", "lon", "lat", "mete_vars", "time",
            "timezone", "horizon_hours",
        }
        _reject_unknown_arguments(args, allowed)
        point, resolved_location = self._resolve_point(args)
        variables = _mete_vars(args.get("mete_vars"))
        timezone = _timezone(args.get("timezone", 8))
        horizon = _horizon(args.get("horizon_hours", 72))
        run_time, time_adjustment = self._forecast_run_time(args.get("time"))
        requested_dataset = _dataset_argument(args.get("dataset", "auto"))
        goal = _choice(
            args.get("selection_goal", "general"),
            "selection_goal",
            {"general", "ai", "high_resolution", "long_range"},
        )
        selection = self._select(
            "forecast", requested_dataset, variables, horizon=horizon, goal=goal
        )
        payload: dict[str, Any] = {
            "lon": point[0], "lat": point[1], "mete_vars": variables, "timezone": timezone,
        }
        if run_time is not None:
            payload["time"] = run_time.strftime(TIME_FORMAT)
        upstream = self._client.query(selection.dataset.key, "point", payload)
        result = _normalize_response(
            upstream,
            selection=selection,
            query_type="point_forecast",
            requested_locations=[list(point)],
            requested_variables=variables,
            timezone=timezone,
            horizon_hours=horizon,
        )
        if resolved_location:
            result["resolved_location"] = resolved_location.as_dict()
        if time_adjustment:
            result["request_adjustments"] = [time_adjustment]
        return result

    def _get_ensemble_forecast(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "dataset", "location", "lon", "lat", "mete_vars", "time", "timezone",
            "horizon_hours", "ensemble_set",
        }
        _reject_unknown_arguments(args, allowed)
        point, resolved_location = self._resolve_point(args)
        variables = _mete_vars(args.get("mete_vars"))
        timezone = _timezone(args.get("timezone", 8))
        horizon = _horizon(args.get("horizon_hours", 72))
        run_time, time_adjustment = self._forecast_run_time(args.get("time"))
        statistics = _ensemble_set(args.get("ensemble_set", ["mean"]))
        requested_dataset = _dataset_argument(args.get("dataset", "auto"))
        selection = self._select(
            "ensemble", requested_dataset, variables, horizon=horizon
        )
        payload: dict[str, Any] = {
            "lon": point[0],
            "lat": point[1],
            "mete_vars": variables,
            "timezone": timezone,
            "ensemble_set": statistics,
        }
        if run_time is not None:
            payload["time"] = run_time.strftime(TIME_FORMAT)
        upstream = self._client.query(selection.dataset.key, "point", payload)
        result = _normalize_response(
            upstream,
            selection=selection,
            query_type="ensemble_forecast",
            requested_locations=[list(point)],
            requested_variables=variables,
            timezone=timezone,
            horizon_hours=horizon,
            ensemble_set=statistics,
        )
        if resolved_location:
            result["resolved_location"] = resolved_location.as_dict()
        if time_adjustment:
            result["request_adjustments"] = [time_adjustment]
        return result

    def _get_point_history(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "dataset", "selection_goal", "location", "lon", "lat", "mete_vars", "start_time",
            "end_time", "timezone",
        }
        _reject_unknown_arguments(args, allowed)
        point, resolved_location = self._resolve_point(args)
        variables = _mete_vars(args.get("mete_vars"))
        timezone = _timezone(args.get("timezone", 8))
        start = _required_time(args.get("start_time"), "start_time")
        end = _required_time(args.get("end_time"), "end_time")
        if end < start:
            raise OpenETMCPError(
                "VALIDATION_ERROR", "end_time must be later than or equal to start_time."
            )
        if end - start > timedelta(days=7):
            raise OpenETMCPError("VALIDATION_ERROR", "History range cannot exceed 7 days.")
        requested_dataset = _dataset_argument(args.get("dataset", "auto"))
        goal = _choice(
            args.get("selection_goal", "general"),
            "selection_goal",
            {"general", "land", "recent"},
        )
        selection = self._select(
            "history", requested_dataset, variables, goal=goal, history_end=end
        )
        payload = {
            "lon": point[0],
            "lat": point[1],
            "mete_vars": variables,
            "start_time": start.strftime(TIME_FORMAT),
            "end_time": end.strftime(TIME_FORMAT),
            "timezone": timezone,
        }
        upstream = self._client.query(selection.dataset.key, "point", payload)
        result = _normalize_response(
            upstream,
            selection=selection,
            query_type="point_history",
            requested_locations=[list(point)],
            requested_variables=variables,
            timezone=timezone,
            history_range=(start, end),
        )
        if resolved_location:
            result["resolved_location"] = resolved_location.as_dict()
        return result

    def _resolve_point(
        self, args: dict[str, Any]
    ) -> tuple[tuple[float, float], ResolvedLocation | None]:
        location = args.get("location")
        has_coordinates = args.get("lon") is not None or args.get("lat") is not None
        if location is not None:
            if has_coordinates:
                raise OpenETMCPError(
                    "VALIDATION_ERROR", "location cannot be combined with lon or lat."
                )
            resolved = self._location_resolver.resolve(str(location))
            return (resolved.longitude, resolved.latitude), resolved
        return _coordinate(args.get("lon"), args.get("lat")), None

    def _forecast_run_time(self, value: object) -> tuple[datetime | None, str | None]:
        run_time = _optional_time(value, "time")
        if run_time is not None and run_time > self._now_utc():
            return None, (
                "已忽略未来的 time；该字段是模式起报时间，不是天气目标日期，"
                "本次改用最新起报时间。"
            )
        return run_time, None

    def _get_multi_point_forecast(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "dataset", "selection_goal", "points", "mete_vars", "time", "timezone",
            "horizon_hours",
        }
        _reject_unknown_arguments(args, allowed)
        points = _points(args.get("points"))
        variables = _mete_vars(args.get("mete_vars"))
        timezone = _timezone(args.get("timezone", 8))
        horizon = _horizon(args.get("horizon_hours", 72))
        run_time, time_adjustment = self._forecast_run_time(args.get("time"))
        requested_dataset = _dataset_argument(args.get("dataset", "auto"))
        goal = _choice(
            args.get("selection_goal", "general"),
            "selection_goal",
            {"general", "ai", "high_resolution", "long_range"},
        )
        selection = self._select(
            "forecast", requested_dataset, variables, horizon=horizon, goal=goal
        )
        if not selection.dataset.supports_multi_point:
            raise OpenETMCPError(
                "OUT_OF_SCOPE", f"Dataset does not support multi-point queries: {selection.dataset.key}"
            )
        payload: dict[str, Any] = {
            "points": [list(point) for point in points],
            "mete_vars": variables,
            "timezone": timezone,
            "avg": False,
        }
        if run_time is not None:
            payload["time"] = run_time.strftime(TIME_FORMAT)
        upstream = self._client.query(selection.dataset.key, "multi/point", payload)
        result = _normalize_response(
            upstream,
            selection=selection,
            query_type="multi_point_forecast",
            requested_locations=[list(point) for point in points],
            requested_variables=variables,
            timezone=timezone,
            horizon_hours=horizon,
        )
        if time_adjustment:
            result["request_adjustments"] = [time_adjustment]
        return result

    def _get_area_average_forecast(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "dataset", "selection_goal", "lon_range", "lat_range", "mete_var", "time",
            "timezone", "horizon_hours",
        }
        _reject_unknown_arguments(args, allowed)
        lon_range = _range(args.get("lon_range"), "lon_range", 72, 137)
        lat_range = _range(args.get("lat_range"), "lat_range", 17, 55)
        lon_span = lon_range[1] - lon_range[0]
        lat_span = lat_range[1] - lat_range[0]
        if lon_span <= 1 or lat_span <= 1:
            raise OpenETMCPError(
                "VALIDATION_ERROR", "Both area spans must be greater than 1 degree."
            )
        if lon_span * lat_span >= 25:
            raise OpenETMCPError(
                "VALIDATION_ERROR", "Area longitude span times latitude span must be below 25."
            )
        variable = _required_string(args, "mete_var")
        variables = _mete_vars([variable])
        timezone = _timezone(args.get("timezone", 8))
        horizon = _horizon(args.get("horizon_hours", 72))
        run_time, time_adjustment = self._forecast_run_time(args.get("time"))
        requested_dataset = _dataset_argument(args.get("dataset", "auto"))
        goal = _choice(
            args.get("selection_goal", "general"),
            "selection_goal",
            {"general", "ai", "high_resolution", "long_range"},
        )
        selection = self._select(
            "forecast", requested_dataset, variables, horizon=horizon, goal=goal
        )
        if not selection.dataset.supports_area_average:
            raise OpenETMCPError(
                "OUT_OF_SCOPE", f"Dataset does not support area-average queries: {selection.dataset.key}"
            )
        payload: dict[str, Any] = {
            "lon_range": list(lon_range),
            "lat_range": list(lat_range),
            "mete_var": variable,
            "timezone": timezone,
            "avg": True,
        }
        if run_time is not None:
            payload["time"] = run_time.strftime(TIME_FORMAT)
        upstream = self._client.query(selection.dataset.key, "area", payload)
        result = _normalize_response(
            upstream,
            selection=selection,
            query_type="area_average_forecast",
            requested_locations=[
                [lon_range[0], lat_range[0]],
                [lon_range[1], lat_range[1]],
            ],
            requested_variables=variables,
            timezone=timezone,
            horizon_hours=horizon,
            require_single_series=True,
        )
        if time_adjustment:
            result["request_adjustments"] = [time_adjustment]
        return result

    def _select(
        self,
        category: DatasetCategory,
        requested_dataset: str,
        variables: list[str],
        *,
        horizon: int | None = None,
        goal: str = "general",
        history_end: datetime | None = None,
    ) -> Selection:
        if requested_dataset != "auto":
            dataset = DATASETS.get(requested_dataset)
            if dataset is None:
                raise OpenETMCPError(
                    "OUT_OF_SCOPE", f"Dataset is not in the OpenET v1 catalog: {requested_dataset}"
                )
            if dataset.category != category:
                raise OpenETMCPError(
                    "OUT_OF_SCOPE",
                    f"Dataset {requested_dataset} is not a {category} dataset.",
                    dataset=requested_dataset,
                )
            if horizon is not None and (
                dataset.max_horizon_hours is None or horizon > dataset.max_horizon_hours
            ):
                raise OpenETMCPError(
                    "VALIDATION_ERROR",
                    f"horizon_hours exceeds {requested_dataset} maximum of "
                    f"{dataset.max_horizon_hours}.",
                    dataset=requested_dataset,
                )
            if not all_variables_supported(dataset, variables):
                raise OpenETMCPError(
                    "VARIABLE_UNAVAILABLE",
                    f"Dataset {requested_dataset} does not support every requested variable or unit.",
                    dataset=requested_dataset,
                )
            alternatives = tuple(
                item.key
                for item in DATASETS.values()
                if item.category == category
                and item.key != dataset.key
                and _compatible(item, variables, horizon)
            )
            return Selection(
                requested_dataset,
                dataset,
                f"Explicit dataset override selected {dataset.key}.",
                alternatives,
            )

        candidates, reason = self._auto_candidates(category, goal, horizon, history_end)
        compatible = [DATASETS[key] for key in candidates if _compatible(DATASETS[key], variables, horizon)]
        if not compatible:
            category_datasets = [item for item in DATASETS.values() if item.category == category]
            variable_exists = any(all_variables_supported(item, variables) for item in category_datasets)
            code = "DATASET_SELECTION_FAILED" if variable_exists else "VARIABLE_UNAVAILABLE"
            raise OpenETMCPError(
                code,
                "No permitted dataset satisfies the requested category, variables, units, and time span.",
            )
        selected = compatible[0]
        selected_reason = reason
        if selected.key != candidates[0]:
            selected_reason += f" Preferred dataset was incompatible; selected {selected.key}."
        alternatives = tuple(item.key for item in compatible[1:])
        return Selection("auto", selected, selected_reason, alternatives)

    def _auto_candidates(
        self,
        category: DatasetCategory,
        goal: str,
        horizon: int | None,
        history_end: datetime | None,
    ) -> tuple[list[str], str]:
        if category == "ensemble":
            if horizon is not None and horizon > 360:
                return ["gefs_p50"], "Extended ensemble horizon uses NOAA GEFS P50."
            return ["ens_open", "gefs_p25", "iconeps_surface"], (
                "Ensemble horizons up to 360 hours prefer ECMWF ENS Open."
            )
        if category == "history":
            if goal == "land":
                return ["era5_land"], "Land and soil history uses ECMWF ERA5-Land."
            if goal == "recent":
                return ["gdas_surface"], "Recent history uses NOAA GDAS."
            if history_end is not None and self._now_utc() - history_end < timedelta(days=7):
                return ["gdas_surface"], "ERA5 delay window requires NOAA GDAS for recent history."
            return ["era5_surface", "gdas_surface"], "General history prefers ECMWF ERA5."
        if goal == "ai":
            if horizon is not None and horizon > 360:
                return ["gfs_graphcast"], "AI horizons above 360 hours use NOAA Graphcast."
            return ["aifs_surface", "gfs_graphcast"], "AI forecasts prefer ECMWF AIFS."
        if goal == "high_resolution":
            if horizon is not None and horizon > 180:
                return ["gfs_surface", "ifs_surface"], (
                    "DWD ICON is limited to 180 hours; falling back to NOAA GFS."
                )
            return ["icon_surface", "gfs_surface", "ifs_surface"], (
                "High-resolution forecasts prefer DWD ICON."
            )
        if goal == "long_range":
            return ["cfs_h6_surface"], "Long-range trends use NOAA CFS 6h."
        return ["gfs_surface", "ifs_surface", "icon_surface"], (
            "General forecasts use NOAA GFS by default."
        )


def _compatible(dataset: DatasetSpec, variables: list[str], horizon: int | None) -> bool:
    if horizon is not None and (
        dataset.max_horizon_hours is None or horizon > dataset.max_horizon_hours
    ):
        return False
    return all_variables_supported(dataset, variables)


def _reject_unknown_arguments(args: dict[str, Any], allowed: set[str]) -> None:
    unexpected = sorted(set(args) - allowed)
    if unexpected:
        raise OpenETMCPError(
            "VALIDATION_ERROR", f"Unsupported argument(s): {', '.join(unexpected)}"
        )


def _required_string(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise OpenETMCPError("VALIDATION_ERROR", f"{name} must be a non-empty string.")
    return value.strip()


def _dataset_argument(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpenETMCPError("VALIDATION_ERROR", "dataset must be a non-empty string.")
    return value.strip()


def _choice(value: object, name: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise OpenETMCPError(
            "VALIDATION_ERROR", f"{name} must be one of: {', '.join(sorted(allowed))}."
        )
    return value


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coordinate(lon: object, lat: object) -> tuple[float, float]:
    if not _is_number(lon) or not _is_number(lat):
        raise OpenETMCPError("VALIDATION_ERROR", "lon and lat must be numbers.")
    longitude = float(lon)
    latitude = float(lat)
    if not 72 <= longitude <= 137 or not 17 <= latitude <= 55:
        raise OpenETMCPError(
            "OUT_OF_SCOPE", "Coordinates must be inside lon 72..137 and lat 17..55."
        )
    return longitude, latitude


def _points(value: object) -> list[tuple[float, float]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 5:
        raise OpenETMCPError("VALIDATION_ERROR", "points must contain 1 to 5 coordinates.")
    result: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise OpenETMCPError(
                "VALIDATION_ERROR", "Each point must be a [lon, lat] pair."
            )
        result.append(_coordinate(item[0], item[1]))
    return result


def _range(value: object, name: str, minimum: float, maximum: float) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2 or not all(_is_number(item) for item in value):
        raise OpenETMCPError(
            "VALIDATION_ERROR", f"{name} must contain exactly two numbers."
        )
    start, end = float(value[0]), float(value[1])
    if not minimum <= start <= maximum or not minimum <= end <= maximum:
        raise OpenETMCPError(
            "OUT_OF_SCOPE", f"{name} endpoints must be inside {minimum}..{maximum}."
        )
    if end <= start:
        raise OpenETMCPError("VALIDATION_ERROR", f"{name} must be strictly increasing.")
    return start, end


def _mete_vars(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 5:
        raise OpenETMCPError("VALIDATION_ERROR", "mete_vars must contain 1 to 5 variables.")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise OpenETMCPError("VALIDATION_ERROR", "Every mete_vars item must be a string.")
    result = [str(item).strip() for item in value]
    if len(set(result)) != len(result):
        raise OpenETMCPError("VALIDATION_ERROR", "mete_vars cannot contain duplicates.")
    return result


def _timezone(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not -12 <= value <= 12:
        raise OpenETMCPError("VALIDATION_ERROR", "timezone must be an integer from -12 to 12.")
    return value


def _horizon(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OpenETMCPError("VALIDATION_ERROR", "horizon_hours must be a positive integer.")
    return value


def _required_time(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not _TIME_PATTERN.fullmatch(value):
        raise OpenETMCPError(
            "VALIDATION_ERROR", f"{name} must use UTC format YYYY-MM-DD HH:mm:ss."
        )
    try:
        return datetime.strptime(value, TIME_FORMAT)
    except ValueError as exc:
        raise OpenETMCPError(
            "VALIDATION_ERROR", f"{name} is not a valid UTC date and time."
        ) from exc


def _optional_time(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    return _required_time(value, name)


def _ensemble_set(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise OpenETMCPError("VALIDATION_ERROR", "ensemble_set must be a non-empty array.")
    if not all(item in {"mean", "min", "max"} for item in value):
        raise OpenETMCPError(
            "VALIDATION_ERROR", "ensemble_set only accepts mean, min, and max."
        )
    result = [str(item) for item in value]
    if len(set(result)) != len(result):
        raise OpenETMCPError("VALIDATION_ERROR", "ensemble_set cannot contain duplicates.")
    return result


def _normalize_response(
    payload: dict[str, Any],
    *,
    selection: Selection,
    query_type: str,
    requested_locations: list[list[float]],
    requested_variables: list[str],
    timezone: int,
    horizon_hours: int | None = None,
    history_range: tuple[datetime, datetime] | None = None,
    ensemble_set: list[str] | None = None,
    require_single_series: bool = False,
) -> dict[str, Any]:
    blocks: list[tuple[str | None, dict[str, Any]]] = []
    if ensemble_set and any(isinstance(payload.get(item), dict) for item in ensemble_set):
        if not all(isinstance(payload.get(item), dict) for item in ensemble_set):
            raise OpenETMCPError(
                "RESPONSE_INVALID", "OpenET ensemble response omits a requested statistic.",
                dataset=selection.dataset.key,
            )
        blocks = [(item, payload[item]) for item in ensemble_set]
    else:
        default_statistic = ensemble_set[0] if ensemble_set and len(ensemble_set) == 1 else None
        blocks = [(default_statistic, payload)]

    expected_timestamps: list[str] | None = None
    expected_run_time: str | None = None
    expected_variables: list[str] | None = None
    expected_units: list[str] | None = None
    series: list[dict[str, Any]] = []
    actual_locations: list[list[float]] = []

    for statistic, block in blocks:
        block_result = _normalize_block(
            block,
            requested_variables=requested_variables,
            statistic=statistic,
            ensemble_set=ensemble_set,
            dataset=selection.dataset.key,
        )
        timestamps = block_result["timestamps"]
        run_time = block_result["run_time"]
        variables = block_result["variables"]
        units = block_result["units"]
        if expected_timestamps is None:
            expected_timestamps = timestamps
            expected_run_time = run_time
            expected_variables = variables
            expected_units = units
        elif (
            timestamps != expected_timestamps
            or run_time != expected_run_time
            or variables != expected_variables
            or units != expected_units
        ):
            raise OpenETMCPError(
                "RESPONSE_INVALID",
                "OpenET ensemble statistic metadata is inconsistent.",
                dataset=selection.dataset.key,
            )
        series.extend(block_result["series"])
        for location in block_result["actual_locations"]:
            if location not in actual_locations:
                actual_locations.append(location)

    if expected_timestamps is None or expected_variables is None or expected_units is None:
        raise OpenETMCPError(
            "RESPONSE_INVALID", "OpenET response has no data blocks.", dataset=selection.dataset.key
        )
    if query_type != "point_history" and not expected_run_time:
        raise OpenETMCPError(
            "RESPONSE_INVALID", "Forecast response is missing time_fcst.",
            dataset=selection.dataset.key,
        )
    indices = _time_indices(
        expected_timestamps,
        run_time_utc=expected_run_time,
        timezone=timezone,
        horizon_hours=horizon_hours,
        history_range=history_range,
        dataset=selection.dataset.key,
    )
    if not indices:
        raise OpenETMCPError(
            "NO_DATA",
            "OpenET returned no points inside the requested time range.",
            dataset=selection.dataset.key,
        )
    for item in series:
        item["values"] = [item["values"][index] for index in indices]
    if require_single_series and len(series) != 1:
        raise OpenETMCPError(
            "RESPONSE_INVALID",
            "Area-average response must contain exactly one aggregated series.",
            dataset=selection.dataset.key,
        )
    result: dict[str, Any] = {
        "requested_dataset": selection.requested_dataset,
        "selected_dataset": selection.dataset.key,
        "selection_reason": selection.reason,
        "alternatives": list(selection.alternatives),
        "query_type": query_type,
        "requested_locations": requested_locations,
        "actual_locations": actual_locations,
        "run_time_utc": expected_run_time,
        "timezone": timezone,
        "variables": expected_variables,
        "units": expected_units,
        "timestamps": [expected_timestamps[index] for index in indices],
        "series": series,
        "total_points": len(expected_timestamps),
        "returned_points": len(indices),
        "truncated": len(indices) < len(expected_timestamps),
    }
    if ensemble_set is not None:
        result["ensemble_set"] = ensemble_set
    return result


def _normalize_block(
    block: dict[str, Any],
    *,
    requested_variables: list[str],
    statistic: str | None,
    ensemble_set: list[str] | None,
    dataset: str,
) -> dict[str, Any]:
    timestamps = block.get("timestamp")
    upstream_variables = block.get("mete_var")
    upstream_units = block.get("mete_unit")
    rows = block.get("data")
    if not isinstance(timestamps, list) or not all(isinstance(item, str) for item in timestamps):
        raise OpenETMCPError(
            "RESPONSE_INVALID", "OpenET response timestamp must be a string array.", dataset=dataset
        )
    if not isinstance(upstream_variables, list) or not all(
        isinstance(item, str) for item in upstream_variables
    ):
        raise OpenETMCPError(
            "RESPONSE_INVALID", "OpenET response mete_var must be a string array.", dataset=dataset
        )
    if not isinstance(upstream_units, list) or not all(isinstance(item, str) for item in upstream_units):
        raise OpenETMCPError(
            "RESPONSE_INVALID", "OpenET response mete_unit must be a string array.", dataset=dataset
        )
    if len(upstream_variables) != len(upstream_units):
        raise OpenETMCPError(
            "RESPONSE_INVALID", "OpenET variable and unit counts differ.", dataset=dataset
        )
    if not isinstance(rows, list) or not rows:
        raise OpenETMCPError("NO_DATA", "OpenET returned no data rows.", dataset=dataset)
    ensemble_columns = _flattened_ensemble_columns(
        upstream_variables, requested_variables, ensemble_set, dataset
    )
    if ensemble_columns:
        first_statistic = ensemble_set[0] if ensemble_set else ""
        units = [upstream_units[index] for index in ensemble_columns[first_statistic]]
        for indices in ensemble_columns.values():
            if [upstream_units[index] for index in indices] != units:
                raise OpenETMCPError(
                    "RESPONSE_INVALID",
                    "OpenET ensemble statistics use inconsistent units.",
                    dataset=dataset,
                )
    else:
        column_indices = _column_indices(upstream_variables, requested_variables, dataset)
        units = [upstream_units[index] for index in column_indices]
    normalized_series: list[dict[str, Any]] = []
    actual_locations: list[list[float]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise OpenETMCPError(
                "RESPONSE_INVALID", "OpenET data row is not an object.", dataset=dataset
            )
        location = row.get("location")
        normalized_location: list[float] | None = None
        if location is not None:
            if (
                not isinstance(location, list)
                or len(location) != 2
                or not all(_is_number(item) for item in location)
            ):
                raise OpenETMCPError(
                    "RESPONSE_INVALID", "OpenET row location is invalid.", dataset=dataset
                )
            normalized_location = [float(location[0]), float(location[1])]
            if normalized_location not in actual_locations:
                actual_locations.append(normalized_location)
        raw_values = row.get("values")
        if ensemble_columns:
            for item, indices in ensemble_columns.items():
                normalized_series.append(
                    _series_item(
                        normalized_location,
                        _normalize_matrix(raw_values, timestamps, indices, dataset),
                        item,
                    )
                )
            continue
        if isinstance(raw_values, dict):
            if not ensemble_set or not all(item in raw_values for item in ensemble_set):
                raise OpenETMCPError(
                    "RESPONSE_INVALID", "OpenET ensemble values omit a requested statistic.",
                    dataset=dataset,
                )
            for item in ensemble_set:
                normalized_series.append(
                    _series_item(
                        normalized_location,
                        _normalize_matrix(raw_values[item], timestamps, column_indices, dataset),
                        item,
                    )
                )
        else:
            if ensemble_set and len(ensemble_set) > 1 and statistic is None:
                raise OpenETMCPError(
                    "RESPONSE_INVALID", "OpenET ensemble response does not identify statistics.",
                    dataset=dataset,
                )
            normalized_series.append(
                _series_item(
                    normalized_location,
                    _normalize_matrix(raw_values, timestamps, column_indices, dataset),
                    statistic,
                )
            )
    return {
        "timestamps": list(timestamps),
        "run_time": _nullable_string(block.get("time_fcst"), "time_fcst", dataset),
        "variables": requested_variables,
        "units": units,
        "series": normalized_series,
        "actual_locations": actual_locations,
    }


def _flattened_ensemble_columns(
    upstream_variables: list[str],
    requested_variables: list[str],
    ensemble_set: list[str] | None,
    dataset: str,
) -> dict[str, list[int]]:
    if not ensemble_set:
        return {}
    has_statistic_suffix = any(
        base_variable_name(name).endswith(f"_{statistic}")
        for name in upstream_variables
        for statistic in ensemble_set
    )
    if not has_statistic_suffix:
        return {}
    if len(upstream_variables) != len(requested_variables) * len(ensemble_set):
        raise OpenETMCPError(
            "VARIABLE_UNAVAILABLE",
            "OpenET ensemble variable count differs from the request.",
            dataset=dataset,
        )
    available = list(range(len(upstream_variables)))
    result: dict[str, list[int]] = {}
    for statistic in ensemble_set:
        indices: list[int] = []
        suffix = f"_{statistic}"
        for requested in requested_variables:
            requested_base = base_variable_name(requested)
            matches = [
                index
                for index in available
                if base_variable_name(upstream_variables[index]) == f"{requested_base}{suffix}"
            ]
            if len(matches) != 1:
                raise OpenETMCPError(
                    "VARIABLE_UNAVAILABLE",
                    f"OpenET response is missing {requested} statistic {statistic}.",
                    dataset=dataset,
                )
            indices.append(matches[0])
            available.remove(matches[0])
        result[statistic] = indices
    return result


def _column_indices(
    upstream_variables: list[str], requested_variables: list[str], dataset: str
) -> list[int]:
    if len(upstream_variables) != len(requested_variables):
        raise OpenETMCPError(
            "VARIABLE_UNAVAILABLE",
            "OpenET response variable count differs from the request.",
            dataset=dataset,
        )
    available = list(range(len(upstream_variables)))
    indices: list[int] = []
    for requested in requested_variables:
        exact = next(
            (index for index in available if upstream_variables[index] == requested),
            None,
        )
        matched = exact
        if matched is None:
            requested_base = base_variable_name(requested)
            base_matches = [
                index
                for index in available
                if base_variable_name(upstream_variables[index]) == requested_base
            ]
            if len(base_matches) == 1:
                matched = base_matches[0]
        if matched is None:
            raise OpenETMCPError(
                "VARIABLE_UNAVAILABLE",
                f"OpenET response is missing requested variable {requested}.",
                dataset=dataset,
            )
        indices.append(matched)
        available.remove(matched)
    return indices


def _normalize_matrix(
    value: object,
    timestamps: list[str],
    column_indices: list[int],
    dataset: str,
) -> list[list[Any]]:
    if not isinstance(value, list) or len(value) != len(timestamps):
        raise OpenETMCPError(
            "RESPONSE_INVALID", "OpenET values and timestamp counts differ.", dataset=dataset
        )
    result: list[list[Any]] = []
    for row in value:
        if not isinstance(row, list) or any(index >= len(row) for index in column_indices):
            raise OpenETMCPError(
                "RESPONSE_INVALID", "OpenET values row does not match mete_var.", dataset=dataset
            )
        result.append([row[index] for index in column_indices])
    return result


def _series_item(
    location: list[float] | None, values: list[list[Any]], statistic: str | None
) -> dict[str, Any]:
    result: dict[str, Any] = {"location": location, "values": values}
    if statistic is not None:
        result["statistic"] = statistic
    return result


def _nullable_string(value: object, field: str, dataset: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OpenETMCPError(
            "RESPONSE_INVALID", f"OpenET response {field} is not a string.", dataset=dataset
        )
    return value


def _time_indices(
    timestamps: list[str],
    *,
    run_time_utc: str | None,
    timezone: int,
    horizon_hours: int | None,
    history_range: tuple[datetime, datetime] | None,
    dataset: str,
) -> list[int]:
    parsed: list[datetime] = []
    for value in timestamps:
        try:
            parsed.append(datetime.strptime(value, TIME_FORMAT))
        except ValueError as exc:
            raise OpenETMCPError(
                "RESPONSE_INVALID", "OpenET timestamp has an unexpected format.", dataset=dataset
            ) from exc
    if history_range is not None:
        start, end = history_range
        local_start = start + timedelta(hours=timezone)
        local_end = end + timedelta(hours=timezone)
        return [index for index, value in enumerate(parsed) if local_start <= value <= local_end]
    if horizon_hours is None or run_time_utc is None:
        return list(range(len(timestamps)))
    try:
        run_time = datetime.strptime(run_time_utc, TIME_FORMAT)
    except ValueError as exc:
        raise OpenETMCPError(
            "RESPONSE_INVALID", "OpenET time_fcst has an unexpected format.", dataset=dataset
        ) from exc
    local_start = run_time + timedelta(hours=timezone)
    local_end = local_start + timedelta(hours=horizon_hours)
    return [index for index, value in enumerate(parsed) if local_start <= value <= local_end]


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_TIME_SCHEMA = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"}
_RUN_TIME_SCHEMA = {
    **_TIME_SCHEMA,
    "description": (
        "可选的数值模式起报时间，不是用户想查询的天气日期。"
        "查询今天、明天或未来天气时必须省略，由系统使用最新起报时间。"
    ),
}
_LOCATION_SCHEMA = {
    "type": "string",
    "description": (
        "用户提供的中国地点名称，例如“北京”“上海市浦东新区”。"
        "优先使用此字段；只有上游系统已经掌握坐标时才改用 lon/lat。"
    ),
}
_LON_SCHEMA = {
    "type": "number",
    "minimum": 72,
    "maximum": 137,
    "description": "系统间调用使用的经度；不得向终端用户索取。",
}
_LAT_SCHEMA = {
    "type": "number",
    "minimum": 17,
    "maximum": 55,
    "description": "系统间调用使用的纬度；不得向终端用户索取。",
}
_VARS_SCHEMA = {
    "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5,
    "uniqueItems": True,
    "description": (
        "气象变量，普通天气查询通常使用 t2m@C（2 米气温）、tp（降水）、"
        "ws10m（10 米风速）和 tcc（总云量），最多 5 个。"
    ),
}
_TIMEZONE_SCHEMA = {"type": "integer", "minimum": -12, "maximum": 12, "default": 8}
_HORIZON_SCHEMA = {
    "type": "integer",
    "minimum": 1,
    "default": 72,
    "description": (
        "从最新起报时间开始返回的小时数；今天通常 24，明天至少 48，"
        "后天至少 72。若不确定可保留默认 72。"
    ),
}
_QUERY_OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "requested_dataset", "selected_dataset", "selection_reason", "query_type",
        "timestamps", "series", "total_points", "returned_points", "truncated",
    ],
}


def _dataset_schema(category: DatasetCategory) -> dict[str, Any]:
    return {
        "type": "string",
        "enum": ["auto", *[item.key for item in DATASETS.values() if item.category == category]],
        "default": "auto",
    }


TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "list_datasets",
        "description": "列出本地收录的 OpenET v1 数据集，不消耗查询额度。",
        "inputSchema": _object_schema(
            {"category": {"type": "string", "enum": ["forecast", "ensemble", "history"]}},
            [],
        ),
        "outputSchema": {"type": "object", "required": ["catalog_version", "datasets"]},
    },
    {
        "name": "describe_dataset",
        "description": "查看指定 OpenET 数据集的变量、单位和使用限制，不消耗查询额度。",
        "inputSchema": _object_schema({"dataset": {"type": "string", "enum": list(DATASETS)}}, ["dataset"]),
        "outputSchema": {"type": "object", "required": ["dataset", "variables"]},
    },
    {
        "name": "get_point_forecast",
        "description": (
            "按地点名称查询气象预报，并按确定性规则自动选择数据集。"
            "用户说城市或区县时直接传 location，不要追问经纬度。"
        ),
        "inputSchema": _object_schema(
            {
                "dataset": _dataset_schema("forecast"),
                "selection_goal": {"type": "string", "enum": ["general", "ai", "high_resolution", "long_range"], "default": "general"},
                "location": _LOCATION_SCHEMA, "lon": _LON_SCHEMA,
                "lat": _LAT_SCHEMA, "mete_vars": _VARS_SCHEMA,
                "time": _RUN_TIME_SCHEMA, "timezone": _TIMEZONE_SCHEMA,
                "horizon_hours": _HORIZON_SCHEMA,
            },
            ["mete_vars"],
        ),
        "outputSchema": _QUERY_OUTPUT_SCHEMA,
    },
    {
        "name": "get_ensemble_forecast",
        "description": (
            "按地点名称查询集合预报统计，默认仅返回集合平均值。"
            "用户说城市或区县时直接传 location，不要追问经纬度。"
        ),
        "inputSchema": _object_schema(
            {
                "dataset": _dataset_schema("ensemble"), "location": _LOCATION_SCHEMA,
                "lon": _LON_SCHEMA,
                "lat": _LAT_SCHEMA,
                "mete_vars": _VARS_SCHEMA, "time": _RUN_TIME_SCHEMA,
                "timezone": _TIMEZONE_SCHEMA, "horizon_hours": _HORIZON_SCHEMA,
                "ensemble_set": {"type": "array", "items": {"type": "string", "enum": ["mean", "min", "max"]}, "minItems": 1, "uniqueItems": True, "default": ["mean"]},
            },
            ["mete_vars"],
        ),
        "outputSchema": _QUERY_OUTPUT_SCHEMA,
    },
    {
        "name": "get_point_history",
        "description": (
            "按地点名称查询最长 7 天的逐小时历史气象数据。"
            "用户说城市或区县时直接传 location，不要追问经纬度。"
        ),
        "inputSchema": _object_schema(
            {
                "dataset": _dataset_schema("history"),
                "selection_goal": {"type": "string", "enum": ["general", "land", "recent"], "default": "general"},
                "location": _LOCATION_SCHEMA, "lon": _LON_SCHEMA,
                "lat": _LAT_SCHEMA, "mete_vars": _VARS_SCHEMA,
                "start_time": _TIME_SCHEMA, "end_time": _TIME_SCHEMA,
                "timezone": _TIMEZONE_SCHEMA,
            },
            ["mete_vars", "start_time", "end_time"],
        ),
        "outputSchema": _QUERY_OUTPUT_SCHEMA,
    },
    {
        "name": "get_multi_point_forecast",
        "description": "一次查询最多 5 个不同经纬度点的气象预报。",
        "inputSchema": _object_schema(
            {
                "dataset": _dataset_schema("forecast"),
                "selection_goal": {"type": "string", "enum": ["general", "ai", "high_resolution", "long_range"], "default": "general"},
                "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}, "minItems": 1, "maxItems": 5},
                "mete_vars": _VARS_SCHEMA, "time": _RUN_TIME_SCHEMA,
                "timezone": _TIMEZONE_SCHEMA, "horizon_hours": _HORIZON_SCHEMA,
            },
            ["points", "mete_vars"],
        ),
        "outputSchema": _QUERY_OUTPUT_SCHEMA,
    },
    {
        "name": "get_area_average_forecast",
        "description": "查询受限矩形区域内单个气象变量的平均预报。",
        "inputSchema": _object_schema(
            {
                "dataset": _dataset_schema("forecast"),
                "selection_goal": {"type": "string", "enum": ["general", "ai", "high_resolution", "long_range"], "default": "general"},
                "lon_range": {"type": "array", "items": _LON_SCHEMA, "minItems": 2, "maxItems": 2},
                "lat_range": {"type": "array", "items": _LAT_SCHEMA, "minItems": 2, "maxItems": 2},
                "mete_var": {"type": "string"}, "time": _RUN_TIME_SCHEMA,
                "timezone": _TIMEZONE_SCHEMA, "horizon_hours": _HORIZON_SCHEMA,
            },
            ["lon_range", "lat_range", "mete_var"],
        ),
        "outputSchema": _QUERY_OUTPUT_SCHEMA,
    },
)
