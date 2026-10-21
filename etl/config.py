"""Model parameters and connection settings (mirrors the notebook's configuration cell)."""
import os
import re
from dataclasses import dataclass, asdict, fields
from pathlib import Path


@dataclass(frozen=True)
class Params:
    crs: str = "EPSG:3763"            # ETRS89 / Portugal TM06
    cell_size: int = 20               # analysis grid (m)
    road_buff: float = 20             # min distance from streets (m)
    slope_max: float = 10             # max slope (%)
    min_area: float = 10000           # min contiguous area (m2) = 1 ha
    w_roads: float = 1.0              # weighted-sum weights (from client brief)
    w_landc: float = 2.0
    w_slope: float = 1.25
    code_field: str = "COSN5"         # land-cover code column; level-1 = first character
    lc_suitable_levels: str = "2,3"   # 2 agriculture, 3 forest/natural
    missing_slope_suitable: bool = False  # notebook: cells with no DEM value -> unsuitable

    def as_dict(self):
        return asdict(self)

    @classmethod
    def with_overrides(cls, pairs):
        """['road_buff=30', 'w_slope=2'] -> Params"""
        casters = {"crs": str, "cell_size": int, "road_buff": float, "slope_max": float,
                   "min_area": float, "w_roads": float, "w_landc": float, "w_slope": float,
                   "code_field": str, "lc_suitable_levels": str,
                   "missing_slope_suitable": lambda v: v.lower() in ("1", "true", "yes")}
        kw = {}
        for pair in pairs or []:
            k, v = pair.split("=", 1)
            if k not in casters:
                raise SystemExit(f"Unknown parameter: {k}")
            kw[k] = casters[k](v)
        return cls(**kw)


def sqlalchemy_url() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql://hotel:hotel@localhost:5432/hotel")
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url)


def psycopg_url() -> str:
    return re.sub(r"^postgresql\+psycopg://", "postgresql://", sqlalchemy_url())


class Paths:
    """Same layout as the notebook's data/ folder."""
    def __init__(self, data_dir="data"):
        d = Path(data_dir)
        self.roads = d / "Lx_Streets" / "gis_osm_roads_free_1.shp"
        self.dem = d / "Lx_elevation" / "elevation_lx.tif"
        self.parishes = d / "Lx_parishes" / "Lx_parishes.shp"
        self.landcover = d / "Lx_land_cover" / "Lx_land_cover_N5.shp"
        self.urban = d / "Lx_urbanization_plan" / "Limit_urban_plan.shp"
        self.sql = Path(__file__).resolve().parent.parent / "sql"
