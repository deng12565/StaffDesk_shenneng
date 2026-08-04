"""版本化的 OpenET 数据集与变量能力目录。

目录内容在 2026-07-31 对照 OpenET 官方文档核验。目录刻意保存在本地，
因此 ``list_datasets``、``describe_dataset`` 和自动选择候选都不消耗上游额度。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CATALOG_VERSION = "2026-07-31"
CATALOG_SOURCE = "https://doc.terraqt.com/s/openet/doc/5pww5o2u5oc76kei-2luiF7Vom6"

DatasetCategory = Literal["forecast", "ensemble", "history"]


# ========== 1. 目录数据结构 ==========


@dataclass(frozen=True)
class VariableSpec:
    name: str
    default_unit: str
    supported_units: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "default_unit": self.default_unit,
            "supported_units": list(self.supported_units),
        }


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    display_name: str
    category: DatasetCategory
    spatial_resolution: str
    temporal_resolution: str
    update_frequency: str
    variables: tuple[VariableSpec, ...]
    max_horizon_hours: int | None = None
    data_delay: str | None = None
    data_delay_hours: int | None = None
    supports_multi_point: bool = False
    supports_area_average: bool = False

    @property
    def variable_names(self) -> frozenset[str]:
        return frozenset(item.name for item in self.variables)

    def as_summary(self) -> dict[str, object]:
        result: dict[str, object] = {
            "dataset": self.key,
            "display_name": self.display_name,
            "category": self.category,
            "spatial_resolution": self.spatial_resolution,
            "temporal_resolution": self.temporal_resolution,
            "update_frequency": self.update_frequency,
        }
        if self.max_horizon_hours is not None:
            result["max_horizon_hours"] = self.max_horizon_hours
        if self.data_delay is not None:
            result["data_delay"] = self.data_delay
        return result

    def as_detail(self) -> dict[str, object]:
        return {
            **self.as_summary(),
            "catalog_version": CATALOG_VERSION,
            "variables": [item.as_dict() for item in self.variables],
            "supports_multi_point": self.supports_multi_point,
            "supports_area_average": self.supports_area_average,
        }


# ========== 2. 变量名称、默认单位和可转换单位注册表 ==========


def _names(value: str) -> tuple[str, ...]:
    return tuple(item for item in value.split() if item)


_VARIABLE_META: dict[str, tuple[str, tuple[str, ...]]] = {}


def _register(names: str, default_unit: str, supported_units: tuple[str, ...] = ()) -> None:
    for name in _names(names):
        _VARIABLE_META[name] = (default_unit, supported_units)


_register(
    "t t2m d2m skt t0m t80m t100m wbt tmax tmin t2m_max t2m_min t_snow t_s "
    "t_so_0 t_so_6 t_so_18 t_so_54 st stl2 stl3 stl4 sot sot2",
    "K",
    ("C", "F"),
)
_register(
    "u10 v10 u10m v10m u20m v20m u30m v30m u40m v40m u50m v50m u80m v80m "
    "u100m v100m gust gust10 ws10m ws20m ws30m ws40m ws50m ws80m ws100m w",
    "m/s",
    ("km/h", "mph"),
)
_register(
    "wd10m wd20m wd30m wd40m wd50m wd80m wd100m",
    "degree",
    ("rad",),
)
_register(
    "dswrf dlwrf uswrf ulwrf ttr ssr ssrd str strd lhf shf nswrf-acc nswrf_top-acc "
    "nswrfcs-acc dswdif-acc uswdif-acc dswdir-acc nswrf nswrf_top nswrfcs dswdif "
    "uswdif dswdir nlwrf nlwrf_top msshf mslhf msnswrf-acc msnswrf msnlwrf "
    "msdrswrf msdwswrf slhf sshf",
    "W/m^2",
)
_register(
    "ttr-acc ssr-acc ssrd-acc str-acc strd-acc slhf-acc sshf-acc",
    "J/m^2",
)
_register(
    "tp tp-acc cp cp-acc sf sf-acc ro ro-acc rowe rain_con rain_con-acc rain_gsp "
    "rain_gsp-acc runoff_g runoff_s snow_con snow_gsp pwat prate watr tcw tcwv tqc tqi "
    "tcolr tcols snd sde sr w_so_0 w_so_1 w_so_3 w_so_9 w_so_27",
    "mm",
    ("m", "inch"),
)
_register("gh1000hpa gh925hpa gh850hpa gh700hpa gh600hpa gh500hpa gh400hpa gh300hpa gh250hpa gh200hpa gh150hpa gh100hpa gh50hpa vis", "m", ("mm", "inch"))
_register("msl sp prmsl mslet vpd", "Pa", ("hPa", "kPa", "MPa"))
_register("tcc lcc mcc hcc rh r2 qv_s sh2 al asn lsm soilw1 soilw2 soilw3 soilw4 soill1 soill2 soill3 soill4", "%", ("[0,1]",))
_register("swvl swvl1 swvl2 swvl3 swvl4 soilw vsw vsw2", "m^3/m^3")
_register("cape cin", "J/kg")
_register("rsn", "kg/m^3")


_PRESSURE_LEVELS = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50)
for _level in _PRESSURE_LEVELS:
    _register(f"t{_level}hpa", "K", ("C", "F"))
    _register(
        f"u{_level}hpa v{_level}hpa ws{_level}hpa w{_level}hpa",
        "m/s",
        ("km/h", "mph"),
    )
    _register(f"wd{_level}hpa", "degree", ("rad",))
    _register(f"q{_level}hpa", "kg/kg")


def _variables(
    names: str,
    overrides: dict[str, tuple[str, tuple[str, ...]]] | None = None,
) -> tuple[VariableSpec, ...]:
    result: list[VariableSpec] = []
    for name in _names(names):
        try:
            default_unit, supported_units = _VARIABLE_META[name]
        except KeyError as exc:  # pragma: no cover - import-time catalog guard
            raise RuntimeError(f"Missing OpenET variable metadata: {name}") from exc
        if overrides and name in overrides:
            default_unit, supported_units = overrides[name]
        result.append(VariableSpec(name, default_unit, supported_units))
    return tuple(result)


_FRACTION_DEFAULT = ("[0,1]", ("%",))
_METRE_DEFAULT = ("m", ("mm", "inch"))
_IFS_UNIT_OVERRIDES = {
    "asn": _FRACTION_DEFAULT,
    "lsm": _FRACTION_DEFAULT,
    "rh": _FRACTION_DEFAULT,
}
_AIFS_UNIT_OVERRIDES = {
    "sf-acc": _METRE_DEFAULT,
    "sf": _METRE_DEFAULT,
    "rowe": _METRE_DEFAULT,
    "tcc": _FRACTION_DEFAULT,
    "lcc": _FRACTION_DEFAULT,
    "mcc": _FRACTION_DEFAULT,
    "hcc": _FRACTION_DEFAULT,
    "rh": _FRACTION_DEFAULT,
}
_GEFS_UNIT_OVERRIDES = {
    "tcc": _FRACTION_DEFAULT,
    "r2": _FRACTION_DEFAULT,
    "sde": _METRE_DEFAULT,
}
_ERA5_UNIT_OVERRIDES = {
    "tcc": _FRACTION_DEFAULT,
    "lcc": _FRACTION_DEFAULT,
    "mcc": _FRACTION_DEFAULT,
    "hcc": _FRACTION_DEFAULT,
    "rh": _FRACTION_DEFAULT,
    "snd": _METRE_DEFAULT,
    "sf": _METRE_DEFAULT,
}
_ERA5_LAND_UNIT_OVERRIDES = {
    "rh": _FRACTION_DEFAULT,
    "snd": _METRE_DEFAULT,
    "sf-acc": _METRE_DEFAULT,
    "sf": _METRE_DEFAULT,
}


_GFS_VARIABLES = (
    "t2m d2m skt t0m t80m t100m wbt u10m v10m u20m v20m u30m v30m u40m v40m "
    "u50m v50m u80m v80m u100m v100m gust ws10m wd10m ws20m wd20m ws30m wd30m "
    "ws40m wd40m ws50m wd50m ws80m wd80m ws100m wd100m tcc lcc mcc hcc dswrf "
    "dlwrf uswrf ulwrf st stl2 stl3 stl4 swvl swvl2 swvl3 swvl4 tp-acc tp snd msl "
    "sp vis rh vpd"
)
_IFS_VARIABLES = (
    "t2m d2m skt wbt u10m v10m u100m v100m ws10m wd10m ws100m wd100m ttr-acc "
    "ssr-acc ssrd-acc str-acc strd-acc ttr ssr ssrd str strd st stl2 stl3 stl4 "
    "swvl1 swvl2 swvl3 swvl4 ro-acc ro tp-acc tp tcwv asn sp msl lsm cape rh vpd"
)
_ENS_VARIABLES = (
    "t2m d2m skt wbt u10m v10m u100m v100m ws10m ws100m ttr-acc ssr-acc "
    "ssrd-acc str-acc strd-acc ttr ssr ssrd str strd st stl2 stl3 stl4 swvl1 "
    "swvl2 swvl3 swvl4 ro-acc ro tp-acc tp tcwv asn sp msl lsm cape rh vpd"
)
_AIFS_VARIABLES = (
    "t2m d2m skt wbt u10m v10m ws10m wd10m u100m v100m ws100m wd100m cp-acc cp "
    "tp-acc tp tcw sf-acc sf rowe sot sot2 vsw vsw2 ssrd-acc strd-acc ssrd strd "
    "tcc lcc mcc hcc sp msl rh vpd"
)
_ICON_VARIABLES = (
    "d2m t2m t2m_max t2m_min t_snow u10m v10m ws10m wd10m gust10 lhf shf nswrf-acc "
    "nswrf_top-acc nswrfcs-acc dswdif-acc uswdif-acc dswdir-acc nswrf nswrf_top "
    "nswrfcs dswdif uswdif dswdir nlwrf nlwrf_top hcc lcc mcc tcc t_s t_so_0 t_so_6 "
    "t_so_18 t_so_54 w_so_0 w_so_1 w_so_3 w_so_9 w_so_27 qv_s rain_con-acc "
    "rain_gsp-acc rain_con rain_gsp runoff_g runoff_s snow_con snow_gsp tqc tqi tcolr "
    "tcols tcwv tp-acc tp prmsl sp rh cape rsn sde sr"
)
_GEFS_P25_VARIABLES = (
    "t2m d2m skt wbt u10 v10 gust ws10m dswrf uswrf dlwrf ulwrf msshf mslhf tcc "
    "vis pwat sde tp-acc tp st soilw sp r2 mslet cape vpd"
)
_GEFS_P50_VARIABLES = (
    "t2m d2m skt wbt u10 v10 ws10m dswrf uswrf dlwrf ulwrf msshf mslhf tcc pwat "
    "sde tp-acc tp st soilw sp prmsl r2 cape vpd w cin"
)
_ERA5_VARIABLES = (
    "t2m d2m skt wbt u10m v10m u100m v100m ws10m wd10m ws100m wd100m tcc lcc mcc "
    "hcc ssrd-acc ssr-acc ssrd ssr msdrswrf msdwswrf st stl2 stl3 stl4 swvl1 "
    "swvl2 swvl3 swvl4 sp msl snd sf rh tp vpd"
)
_ERA5_LAND_VARIABLES = (
    "t2m d2m skt wbt u10m v10m ws10m wd10m ssr-acc str-acc ssrd-acc strd-acc "
    "slhf-acc sshf-acc ssr str ssrd strd slhf sshf st stl2 stl3 stl4 swvl1 swvl2 "
    "swvl3 swvl4 sp snd sf-acc sf rh tp-acc tp vpd"
)

_graphcast_variables: list[str] = ["t2m", "u10", "v10", "ws10m", "wd10m"]
for _prefix in ("t", "u", "v", "ws", "wd", "w", "q", "gh"):
    _graphcast_variables.extend(f"{_prefix}{level}hpa" for level in _PRESSURE_LEVELS)
_graphcast_variables.extend(("tcc", "lcc", "mcc", "hcc"))


# ========== 3. 首版允许使用的数据集白名单 ==========
#
# OpenETService 只会从这里选择数据集；即使 LLM 或调用方传入其他名称，
# 服务层也会拒绝，避免绕过开源数据集和查询能力边界。

DATASETS: dict[str, DatasetSpec] = {
    "gfs_surface": DatasetSpec(
        "gfs_surface", "NOAA GFS", "forecast", "0.25 degree", "1h to 120h, then 3h",
        "4 times daily", _variables(_GFS_VARIABLES), 384, supports_multi_point=True,
        supports_area_average=True,
    ),
    "gfs_graphcast": DatasetSpec(
        "gfs_graphcast", "NOAA Graphcast", "forecast", "0.25 degree", "6h",
        "4 times daily", _variables(" ".join(_graphcast_variables)), 384,
        supports_multi_point=True, supports_area_average=True,
    ),
    "cfs_h6_surface": DatasetSpec(
        "cfs_h6_surface", "NOAA CFS 6h", "forecast", "1 degree", "6h",
        "4 times daily",
        _variables(
            "t t2m tmax tmin u10 v10 ws10m wd10m tcc lcc mcc hcc dswrf dlwrf "
            "uswrf ulwrf pwat prate soilw1 soilw2 soilw3 soilw4 soill1 soill2 "
            "soill3 soill4 sh2 sp sde watr al",
            overrides={"sh2": _FRACTION_DEFAULT, "sde": _METRE_DEFAULT},
        ),
        5040, supports_multi_point=True, supports_area_average=True,
    ),
    "ifs_surface": DatasetSpec(
        "ifs_surface", "ECMWF IFS Open", "forecast", "0.25 degree", "3h to 144h, then 6h",
        "4 times daily", _variables(_IFS_VARIABLES, _IFS_UNIT_OVERRIDES), 240,
        supports_multi_point=True,
        supports_area_average=True,
    ),
    "aifs_surface": DatasetSpec(
        "aifs_surface", "ECMWF AIFS", "forecast", "0.25 degree", "6h",
        "4 times daily", _variables(_AIFS_VARIABLES, _AIFS_UNIT_OVERRIDES), 360,
        supports_multi_point=True,
        supports_area_average=True,
    ),
    "icon_surface": DatasetSpec(
        "icon_surface", "DWD ICON", "forecast", "0.125 degree", "1h to 78h, then 3h",
        "4 times daily",
        _variables(_ICON_VARIABLES, {"sde": _METRE_DEFAULT, "sr": _METRE_DEFAULT}),
        180, supports_multi_point=True,
        supports_area_average=True,
    ),
    "gefs_p25": DatasetSpec(
        "gefs_p25", "NOAA GEFS P25", "ensemble", "0.25 degree", "3h",
        "4 times daily", _variables(_GEFS_P25_VARIABLES, _GEFS_UNIT_OVERRIDES), 240,
    ),
    "gefs_p50": DatasetSpec(
        "gefs_p50", "NOAA GEFS P50", "ensemble", "0.5 degree", "3h to 240h, then 6h",
        "daily", _variables(_GEFS_P50_VARIABLES, _GEFS_UNIT_OVERRIDES), 840,
    ),
    "ens_open": DatasetSpec(
        "ens_open", "ECMWF ENS Open", "ensemble", "0.25 degree", "3h to 144h, then 6h",
        "4 times daily", _variables(_ENS_VARIABLES, _IFS_UNIT_OVERRIDES), 360,
    ),
    "iconeps_surface": DatasetSpec(
        "iconeps_surface", "DWD ICON EPS", "ensemble", "0.25 degree",
        "1h to 48h, 3h to 72h, 6h to 120h, then 12h", "2 times daily",
        _variables(
            "t2m d2m u10m v10m ws10m wd10m msnswrf-acc nswrf msnswrf msnlwrf "
            "nlwrf tcc rh tp-acc tp sp"
        ),
        180,
    ),
    "era5_surface": DatasetSpec(
        "era5_surface", "ECMWF ERA5", "history", "0.25 degree", "1h", "daily",
        _variables(_ERA5_VARIABLES, _ERA5_UNIT_OVERRIDES),
        data_delay="about 7 days", data_delay_hours=168,
    ),
    "era5_land": DatasetSpec(
        "era5_land", "ECMWF ERA5-Land", "history", "0.1 degree", "1h", "daily",
        _variables(_ERA5_LAND_VARIABLES, _ERA5_LAND_UNIT_OVERRIDES),
        data_delay="about 7 days", data_delay_hours=168,
    ),
    "gdas_surface": DatasetSpec(
        "gdas_surface", "NOAA GDAS", "history", "0.25 degree", "1h", "daily",
        _variables(_GFS_VARIABLES), data_delay="about 1 day", data_delay_hours=24,
    ),
}


# ========== 4. 服务层使用的目录查询辅助函数 ==========


def base_variable_name(requested_name: str) -> str:
    return requested_name.split("@", 1)[0]


def variable_supported(dataset: DatasetSpec, requested_name: str) -> bool:
    base_name, separator, requested_unit = requested_name.partition("@")
    variable = next((item for item in dataset.variables if item.name == base_name), None)
    if variable is None:
        return False
    if not separator:
        return True
    normalized = requested_unit.replace(" ", "")
    return normalized in {unit.replace(" ", "") for unit in variable.supported_units}


def all_variables_supported(dataset: DatasetSpec, requested_names: list[str]) -> bool:
    return all(variable_supported(dataset, name) for name in requested_names)
