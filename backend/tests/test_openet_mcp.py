from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.internal_service import INTERNAL_SERVICE_HEADER, internal_service_token
from app.tools import mcp_client as mcp_client_module
from app.tools.mcp_client import list_mcp_tools
from app.tools.openet_mcp.catalog import CATALOG_VERSION, DATASETS
from app.tools.openet_mcp.http import router as openet_http_router
from app.tools.openet_mcp.protocol import main as protocol_main
from app.tools.openet_mcp.service import (
    TOOL_DEFINITIONS,
    LocationResolver,
    OpenETClient,
    OpenETMCPError,
    OpenETService,
    load_openet_token,
)

NOW = datetime(2026, 7, 31, 12, 0, 0)
FAKE_TOKEN = "unit-test-token-not-a-secret"


def _upstream_payload(
    *,
    variables: list[str] | None = None,
    units: list[str] | None = None,
    timestamps: list[str] | None = None,
    locations: list[list[float]] | None = None,
    values: list[list[list[Any]]] | None = None,
    run_time: str | None = "2026-07-31 00:00:00",
) -> dict[str, Any]:
    selected_variables = variables or ["t2m"]
    selected_units = units or ["K"]
    selected_timestamps = timestamps or ["2026-07-31 08:00:00"]
    selected_locations = locations or [[116.5, 40.0]]
    selected_values = values or [
        [[float(row_index + column_index + 1) for column_index in range(len(selected_variables))]
         for row_index in range(len(selected_timestamps))]
        for _ in selected_locations
    ]
    payload: dict[str, Any] = {
        "data": [
            {"location": location, "values": row_values}
            for location, row_values in zip(selected_locations, selected_values, strict=True)
        ],
        "mete_var": selected_variables,
        "mete_unit": selected_units,
        "timestamp": selected_timestamps,
    }
    if run_time is not None:
        payload["time_fcst"] = run_time
    return payload


def _success_response(**kwargs: Any) -> dict[str, Any]:
    return {"success": True, "code": 200, "msg": "ok", "data": _upstream_payload(**kwargs)}


class RecordingUpstream:
    def __init__(self, response: httpx.Response | dict[str, Any] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.response = response or _success_response()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, httpx.Response):
            return self.response
        return httpx.Response(200, json=self.response)


def _service(
    upstream: RecordingUpstream | None = None,
    *,
    token: str = FAKE_TOKEN,
    location_resolver: LocationResolver | None = None,
) -> tuple[OpenETService, RecordingUpstream]:
    selected = upstream or RecordingUpstream()
    client = OpenETClient(
        token=token,
        base_url="https://openet.test/v1",
        transport=httpx.MockTransport(selected),
    )
    return OpenETService(
        client,
        now_utc=lambda: NOW,
        location_resolver=location_resolver,
    ), selected


def _forecast_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"lon": 116.4, "lat": 39.9, "mete_vars": ["t2m"]}
    args.update(overrides)
    return args


def _assert_error(code: str, callback) -> OpenETMCPError:
    with pytest.raises(OpenETMCPError) as exc_info:
        callback()
    assert exc_info.value.code == code
    return exc_info.value


def test_catalog_contains_only_the_thirteen_v1_datasets() -> None:
    assert CATALOG_VERSION == "2026-07-31"
    assert set(DATASETS) == {
        "gfs_surface",
        "gfs_graphcast",
        "cfs_h6_surface",
        "ifs_surface",
        "aifs_surface",
        "icon_surface",
        "gefs_p25",
        "gefs_p50",
        "ens_open",
        "iconeps_surface",
        "era5_surface",
        "era5_land",
        "gdas_surface",
    }


def test_tools_list_contract_has_exactly_seven_bounded_tools() -> None:
    names = [item["name"] for item in TOOL_DEFINITIONS]
    assert names == [
        "list_datasets",
        "describe_dataset",
        "get_point_forecast",
        "get_ensemble_forecast",
        "get_point_history",
        "get_multi_point_forecast",
        "get_area_average_forecast",
    ]
    for definition in TOOL_DEFINITIONS:
        assert definition["description"]
        assert definition["inputSchema"]["additionalProperties"] is False
        assert "outputSchema" in definition
    area_schema = TOOL_DEFINITIONS[-1]["inputSchema"]["properties"]
    assert "avg" not in area_schema
    assert "raw_request" not in area_schema
    point_datasets = TOOL_DEFINITIONS[2]["inputSchema"]["properties"]["dataset"]["enum"]
    ensemble_datasets = TOOL_DEFINITIONS[3]["inputSchema"]["properties"]["dataset"]["enum"]
    history_datasets = TOOL_DEFINITIONS[4]["inputSchema"]["properties"]["dataset"]["enum"]
    assert point_datasets == [
        "auto", "gfs_surface", "gfs_graphcast", "cfs_h6_surface", "ifs_surface",
        "aifs_surface", "icon_surface",
    ]
    assert ensemble_datasets == [
        "auto", "gefs_p25", "gefs_p50", "ens_open", "iconeps_surface"
    ]
    assert history_datasets == ["auto", "era5_surface", "era5_land", "gdas_surface"]
    for index in (2, 3, 4):
        point_schema = TOOL_DEFINITIONS[index]["inputSchema"]
        assert "location" in point_schema["properties"]
        assert "mete_vars" in point_schema["required"]
        assert "lon" not in point_schema["required"]
        assert "lat" not in point_schema["required"]
    forecast_time = TOOL_DEFINITIONS[2]["inputSchema"]["properties"]["time"]
    assert "天气日期" in forecast_time["description"]
    assert "必须省略" in forecast_time["description"]


def test_dataset_descriptions_preserve_dataset_specific_unit_defaults() -> None:
    gfs_rh = next(item for item in DATASETS["gfs_surface"].variables if item.name == "rh")
    ifs_rh = next(item for item in DATASETS["ifs_surface"].variables if item.name == "rh")
    era5_snow = next(item for item in DATASETS["era5_surface"].variables if item.name == "snd")

    assert (gfs_rh.default_unit, gfs_rh.supported_units) == ("%", ("[0,1]",))
    assert (ifs_rh.default_unit, ifs_rh.supported_units) == ("[0,1]", ("%",))
    assert (era5_snow.default_unit, era5_snow.supported_units) == ("m", ("mm", "inch"))


def test_staffdeck_stdio_client_discovers_the_real_openet_module() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    tools = list_mcp_tools(
        {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "app.tools.openet_mcp"],
            "cwd": str(backend_dir),
            "env": {},
        },
        timeout_seconds=15,
    )
    assert [item["name"] for item in tools] == [item["name"] for item in TOOL_DEFINITIONS]


def test_stdio_protocol_initialize_notification_list_and_local_call() -> None:
    source = io.StringIO(
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "list_datasets", "arguments": {"category": "history"}},
                    }
                ),
            ]
        )
        + "\n"
    )
    target = io.StringIO()

    protocol_main(source, target, OpenETService(OpenETClient(token="")))

    responses = [json.loads(line) for line in target.getvalue().splitlines()]
    assert [item["id"] for item in responses] == [1, 2, 3]
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert len(responses[1]["result"]["tools"]) == 7
    call_result = responses[2]["result"]
    assert call_result["isError"] is False
    content = json.loads(call_result["content"][0]["text"])
    assert {item["dataset"] for item in content["datasets"]} == {
        "era5_surface", "era5_land", "gdas_surface"
    }


def test_streamable_http_route_requires_internal_auth_and_serves_all_mcp_steps() -> None:
    app = FastAPI()
    app.include_router(openet_http_router)
    client = TestClient(app)
    endpoint = "/api/mcp/openet"

    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    assert client.post(endpoint, json=initialize).status_code == 401

    headers = {INTERNAL_SERVICE_HEADER: internal_service_token()}
    initialized = client.post(endpoint, json=initialize, headers=headers)
    assert initialized.status_code == 200
    assert initialized.json()["result"]["protocolVersion"] == "2024-11-05"

    notification = client.post(
        endpoint,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=headers,
    )
    assert notification.status_code == 202

    listed = client.post(
        endpoint,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=headers,
    )
    assert [item["name"] for item in listed.json()["result"]["tools"]] == [
        item["name"] for item in TOOL_DEFINITIONS
    ]

    called = client.post(
        endpoint,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_datasets", "arguments": {"category": "history"}},
        },
        headers=headers,
    )
    result = called.json()["result"]
    assert result["isError"] is False
    assert len(result["structuredContent"]["datasets"]) == 3


def test_http_client_adds_internal_auth_only_for_configured_openet_origin(monkeypatch) -> None:
    settings = type(
        "Settings",
        (),
        {"normalized_tool_base_url": "http://127.0.0.1:5173"},
    )()
    monkeypatch.setattr(mcp_client_module, "get_settings", lambda: settings)

    internal = mcp_client_module._HttpSession(
        {"url": "http://127.0.0.1:5173/api/mcp/openet"},
        8,
    )._headers()
    external = mcp_client_module._HttpSession(
        {"url": "https://example.test/api/mcp/openet"},
        8,
    )._headers()

    assert internal[INTERNAL_SERVICE_HEADER] == internal_service_token()
    assert INTERNAL_SERVICE_HEADER not in external


def test_local_catalog_tools_never_call_upstream() -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        raise AssertionError("local catalog tool called upstream")

    service = OpenETService(
        OpenETClient(token="", transport=httpx.MockTransport(unexpected)),
        now_utc=lambda: NOW,
    )

    listed = service.call("list_datasets", {"category": "forecast"})
    described = service.call("describe_dataset", {"dataset": "gfs_surface"})

    assert len(listed["datasets"]) == 6
    assert described["dataset"] == "gfs_surface"
    assert any(item["name"] == "t2m" for item in described["variables"])
    assert described["supports_area_average"] is True


@pytest.mark.parametrize(
    ("lon", "lat"),
    [(72, 17), (72, 55), (137, 17), (137, 55)],
)
def test_coordinate_boundaries_are_inclusive(lon: float, lat: float) -> None:
    service, _ = _service()
    result = service.call("get_point_forecast", _forecast_args(lon=lon, lat=lat))
    assert result["requested_locations"] == [[float(lon), float(lat)]]


def test_point_forecast_resolves_human_location_without_coordinates() -> None:
    geocoding_requests: list[httpx.Request] = []

    def geocode(request: httpx.Request) -> httpx.Response:
        geocoding_requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "lat": "39.9057136",
                    "lon": "116.3912972",
                    "name": "北京市",
                    "display_name": "北京市, 中国",
                }
            ],
        )

    resolver = LocationResolver(
        base_url="https://geocoding.test/search",
        transport=httpx.MockTransport(geocode),
    )
    service, upstream = _service(
        RecordingUpstream(_success_response(variables=["t2m", "tp"], units=["C", "mm"])),
        location_resolver=resolver,
    )

    result = service.call(
        "get_point_forecast",
        {"location": "北京", "mete_vars": ["t2m@C", "tp"], "horizon_hours": 48},
    )

    assert len(geocoding_requests) == 1
    assert geocoding_requests[0].url.params["q"] == "北京"
    assert result["requested_locations"] == [[116.3912972, 39.9057136]]
    assert result["resolved_location"]["display_name"] == "北京市, 中国"
    request_body = json.loads(upstream.requests[0].content)
    assert request_body["lon"] == 116.3912972
    assert request_body["lat"] == 39.9057136


def test_location_resolver_requests_human_region_context_for_ambiguous_place() -> None:
    def geocode(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "lat": "41.5754",
                    "lon": "120.4390",
                    "display_name": "朝阳市, 辽宁省, 中国",
                },
                {
                    "lat": "39.9204",
                    "lon": "116.4369",
                    "display_name": "朝阳区, 北京市, 中国",
                },
            ],
        )

    resolver = LocationResolver(
        base_url="https://geocoding.test/search",
        transport=httpx.MockTransport(geocode),
    )

    error = _assert_error("LOCATION_AMBIGUOUS", lambda: resolver.resolve("朝阳"))

    assert "省份或城市" in error.message
    assert "经纬度" not in error.message


def test_location_and_coordinates_cannot_be_combined() -> None:
    service, upstream = _service()

    error = _assert_error(
        "VALIDATION_ERROR",
        lambda: service.call(
            "get_point_forecast",
            {"location": "北京", "lon": 116.4, "mete_vars": ["t2m"]},
        ),
    )

    assert "cannot be combined" in error.message
    assert upstream.requests == []


def test_future_target_date_misplaced_in_run_time_is_ignored_locally() -> None:
    service, upstream = _service()

    result = service.call(
        "get_point_forecast",
        _forecast_args(time="2026-08-01 00:00:00", horizon_hours=48),
    )

    request_body = json.loads(upstream.requests[0].content)
    assert "time" not in request_body
    assert "未来的 time" in result["request_adjustments"][0]


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (_forecast_args(lon=71.999), "OUT_OF_SCOPE"),
        (_forecast_args(lat=55.001), "OUT_OF_SCOPE"),
        (_forecast_args(mete_vars=[]), "VALIDATION_ERROR"),
        (_forecast_args(mete_vars=["t2m", "d2m", "skt", "wbt", "tp", "sp"]), "VALIDATION_ERROR"),
        (_forecast_args(mete_vars=["not_a_weather_variable"]), "VARIABLE_UNAVAILABLE"),
    ],
)
def test_point_validation_rejects_invalid_scope_and_variables(
    args: dict[str, Any], code: str
) -> None:
    service, upstream = _service()
    _assert_error(code, lambda: service.call("get_point_forecast", args))
    assert upstream.requests == []


@pytest.mark.parametrize(
    ("category", "goal", "horizon", "history_end", "expected"),
    [
        ("forecast", "general", 72, None, "gfs_surface"),
        ("forecast", "ai", 360, None, "aifs_surface"),
        ("forecast", "ai", 361, None, "gfs_graphcast"),
        ("forecast", "high_resolution", 180, None, "icon_surface"),
        ("forecast", "high_resolution", 181, None, "gfs_surface"),
        ("forecast", "long_range", 1000, None, "cfs_h6_surface"),
        ("ensemble", "general", 360, None, "ens_open"),
        ("ensemble", "general", 361, None, "gefs_p50"),
        ("history", "general", None, NOW - timedelta(days=10), "era5_surface"),
        ("history", "general", None, NOW - timedelta(days=2), "gdas_surface"),
        ("history", "recent", None, NOW - timedelta(days=10), "gdas_surface"),
        ("history", "land", None, NOW - timedelta(days=10), "era5_land"),
    ],
)
def test_auto_selection_rules(
    category: str,
    goal: str,
    horizon: int | None,
    history_end: datetime | None,
    expected: str,
) -> None:
    service, _ = _service()
    selection = service._select(  # noqa: SLF001 - selection is the behavior under test
        category,  # type: ignore[arg-type]
        "auto",
        ["t2m"],
        goal=goal,
        horizon=horizon,
        history_end=history_end,
    )
    assert selection.dataset.key == expected
    assert selection.reason


def test_general_selection_falls_back_by_variable_without_fanout() -> None:
    upstream = RecordingUpstream(
        _success_response(variables=["ssr"], units=["W/m^2"])
    )
    service, recorder = _service(upstream)

    result = service.call("get_point_forecast", _forecast_args(mete_vars=["ssr"]))

    assert result["selected_dataset"] == "ifs_surface"
    assert "incompatible" in result["selection_reason"]
    assert len(recorder.requests) == 1
    assert recorder.requests[0].url.path == "/v1/ifs_surface/point"


def test_explicit_dataset_wins_and_incompatible_explicit_values_fail_locally() -> None:
    service, recorder = _service()
    result = service.call(
        "get_point_forecast",
        _forecast_args(dataset="ifs_surface", selection_goal="ai"),
    )
    assert result["selected_dataset"] == "ifs_surface"
    assert len(recorder.requests) == 1

    _assert_error(
        "OUT_OF_SCOPE",
        lambda: service.call("get_ensemble_forecast", _forecast_args(dataset="gfs_surface")),
    )
    _assert_error(
        "VALIDATION_ERROR",
        lambda: service.call(
            "get_point_forecast", _forecast_args(dataset="icon_surface", horizon_hours=181)
        ),
    )
    _assert_error(
        "DATASET_SELECTION_FAILED",
        lambda: service.call(
            "get_point_forecast",
            _forecast_args(selection_goal="long_range", mete_vars=["d2m"]),
        ),
    )
    assert len(recorder.requests) == 1


def test_default_horizon_truncates_without_changing_values() -> None:
    timestamps = [
        (datetime(2026, 7, 31, 8) + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S")
        for index in range(1, 101)
    ]
    values = [[[index] for index in range(1, 101)]]
    service, _ = _service(
        RecordingUpstream(_success_response(timestamps=timestamps, values=values))
    )

    result = service.call("get_point_forecast", _forecast_args())

    assert result["total_points"] == 100
    assert result["returned_points"] == 72
    assert result["truncated"] is True
    assert result["series"][0]["values"] == [[index] for index in range(1, 73)]


def test_ensemble_defaults_to_mean_and_sends_one_request() -> None:
    upstream = RecordingUpstream()
    service, recorder = _service(upstream)

    result = service.call("get_ensemble_forecast", _forecast_args())

    assert result["selected_dataset"] == "ens_open"
    assert result["ensemble_set"] == ["mean"]
    assert result["series"][0]["statistic"] == "mean"
    assert len(recorder.requests) == 1
    request_body = json.loads(recorder.requests[0].content)
    assert request_body["ensemble_set"] == ["mean"]


def test_ensemble_flattened_statistic_columns_are_split_into_series() -> None:
    response = _success_response(
        variables=["d2m_min", "t2m_mean", "t2m_min", "d2m_mean"],
        units=["K", "C", "C", "K"],
        values=[[[275.0, 20.0, 18.0, 280.0]]],
    )
    service, _ = _service(RecordingUpstream(response))

    result = service.call(
        "get_ensemble_forecast",
        _forecast_args(
            mete_vars=["t2m@C", "d2m"],
            ensemble_set=["mean", "min"],
        ),
    )

    assert result["variables"] == ["t2m@C", "d2m"]
    assert result["units"] == ["C", "K"]
    assert result["series"] == [
        {"location": [116.5, 40.0], "values": [[20.0, 280.0]], "statistic": "mean"},
        {"location": [116.5, 40.0], "values": [[18.0, 275.0]], "statistic": "min"},
    ]


def test_history_validates_order_and_seven_day_limit_before_upstream() -> None:
    service, recorder = _service()
    common = {
        "lon": 116.4,
        "lat": 39.9,
        "mete_vars": ["t2m"],
    }
    _assert_error(
        "VALIDATION_ERROR",
        lambda: service.call(
            "get_point_history",
            {**common, "start_time": "2026-07-20 01:00:00", "end_time": "2026-07-20 00:00:00"},
        ),
    )
    _assert_error(
        "VALIDATION_ERROR",
        lambda: service.call(
            "get_point_history",
            {**common, "start_time": "2026-07-20 00:00:00", "end_time": "2026-07-27 00:00:01"},
        ),
    )
    assert recorder.requests == []


def test_multi_point_limits_and_preserves_requested_and_actual_locations() -> None:
    service, recorder = _service()
    six_points = [[100 + index, 30] for index in range(6)]
    _assert_error(
        "VALIDATION_ERROR",
        lambda: service.call(
            "get_multi_point_forecast", {"points": six_points, "mete_vars": ["t2m"]}
        ),
    )
    assert recorder.requests == []

    locations = [[116.5, 40.0], [121.0, 31.25]]
    values = [[[[1.0]][0]], [[[2.0]][0]]]
    success = _success_response(locations=locations, values=values)
    service, recorder = _service(RecordingUpstream(success))
    requested = [[116.4, 39.9], [121.1, 31.2]]
    result = service.call(
        "get_multi_point_forecast", {"points": requested, "mete_vars": ["t2m"]}
    )
    assert result["requested_locations"] == requested
    assert result["actual_locations"] == locations
    assert [item["location"] for item in result["series"]] == locations
    request_body = json.loads(recorder.requests[0].content)
    assert request_body["avg"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"lon_range": [100, 101], "lat_range": [20, 22]},
        {"lon_range": [100, 105], "lat_range": [20, 25]},
        {"lon_range": [100, 102], "lat_range": [20, 22], "avg": False},
    ],
)
def test_area_rejects_small_large_and_raw_grid_requests(overrides: dict[str, Any]) -> None:
    service, recorder = _service()
    args = {"lon_range": [100, 102], "lat_range": [20, 22], "mete_var": "t2m"}
    args.update(overrides)
    _assert_error("VALIDATION_ERROR", lambda: service.call("get_area_average_forecast", args))
    assert recorder.requests == []


def test_area_always_forces_upstream_average() -> None:
    service, recorder = _service()
    service.call(
        "get_area_average_forecast",
        {"lon_range": [100, 102], "lat_range": [20, 22], "mete_var": "t2m"},
    )
    body = json.loads(recorder.requests[0].content)
    assert body["avg"] is True
    assert body["mete_var"] == "t2m"


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        ({"success": False, "code": "E10201", "msg": "outside region"}, "OUT_OF_SCOPE"),
        ({"success": False, "code": "E10301", "msg": "no data"}, "NO_DATA"),
        ({"success": False, "code": "E10400", "msg": "variable missing"}, "VARIABLE_UNAVAILABLE"),
    ],
)
def test_http_200_business_errors_are_not_treated_as_success(
    response: dict[str, Any], expected_code: str
) -> None:
    service, _ = _service(RecordingUpstream(response))
    _assert_error(expected_code, lambda: service.call("get_point_forecast", _forecast_args()))


def test_non_json_timeout_missing_token_and_rejected_token_are_classified() -> None:
    non_json = RecordingUpstream(httpx.Response(200, text="not-json"))
    service, _ = _service(non_json)
    _assert_error("RESPONSE_INVALID", lambda: service.call("get_point_forecast", _forecast_args()))

    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow upstream")

    timeout_service = OpenETService(
        OpenETClient(token=FAKE_TOKEN, transport=httpx.MockTransport(timeout))
    )
    _assert_error(
        "UPSTREAM_TIMEOUT",
        lambda: timeout_service.call("get_point_forecast", _forecast_args()),
    )

    missing_service, _ = _service(token="")
    _assert_error(
        "AUTH_MISSING", lambda: missing_service.call("get_point_forecast", _forecast_args())
    )

    rejected_service, _ = _service(RecordingUpstream(httpx.Response(403, text="denied")))
    _assert_error(
        "AUTH_REJECTED", lambda: rejected_service.call("get_point_forecast", _forecast_args())
    )


def test_upstream_error_message_redacts_the_configured_token() -> None:
    body = {
        "success": False,
        "code": "E50000",
        "msg": f"request failed token={FAKE_TOKEN}",
    }
    service, _ = _service(RecordingUpstream(body))

    error = _assert_error(
        "AUTH_REJECTED", lambda: service.call("get_point_forecast", _forecast_args())
    )

    assert FAKE_TOKEN not in error.message
    assert "[REDACTED]" in error.message


def test_response_variable_order_is_normalized_without_changing_values() -> None:
    response = _success_response(
        variables=["d2m", "t2m"],
        units=["K", "C"],
        values=[[[280.0, 20.0]]],
    )
    service, _ = _service(RecordingUpstream(response))

    result = service.call(
        "get_point_forecast", _forecast_args(mete_vars=["t2m@C", "d2m"])
    )

    assert result["variables"] == ["t2m@C", "d2m"]
    assert result["units"] == ["C", "K"]
    assert result["series"][0]["values"] == [[20.0, 280.0]]


def test_partial_variable_response_is_an_explicit_error() -> None:
    service, _ = _service(RecordingUpstream(_success_response(variables=["t2m"])))
    _assert_error(
        "VARIABLE_UNAVAILABLE",
        lambda: service.call(
            "get_point_forecast", _forecast_args(mete_vars=["t2m", "d2m"])
        ),
    )


def test_dotenv_token_loader_reads_only_the_selected_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENET_API_TOKEN=local-file-token\n", encoding="utf-8")
    assert load_openet_token(env_file) == "local-file-token"


def test_protocol_tool_error_is_structured_and_does_not_emit_a_traceback() -> None:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_point_forecast", "arguments": _forecast_args()},
    }
    target = io.StringIO()

    protocol_main(
        io.StringIO(json.dumps(request) + "\n"),
        target,
        OpenETService(OpenETClient(token="")),
    )

    response = json.loads(target.getvalue())
    result = response["result"]
    assert result["isError"] is True
    error = json.loads(result["content"][0]["text"])["error"]
    assert error["code"] == "AUTH_MISSING"
    assert "Traceback" not in target.getvalue()
