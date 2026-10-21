"""ETL entry point:  python -m etl.run [--data-dir data] [--skip-raw] [--set road_buff=30] [--label text]"""
import argparse
import datetime as dt
import logging
import sys
import psycopg
from sqlalchemy import create_engine

from .config import Params, Paths, sqlalchemy_url, psycopg_url


def init_schema(paths):
    with psycopg.connect(psycopg_url(), autocommit=True) as conn:
        for f in sorted(paths.sql.glob("*.sql")):
            conn.execute(f.read_text())          # no params -> multi-statement allowed
            logging.getLogger("etl").info("applied %s", f.name)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--skip-raw", action="store_true", help="reuse raw.* tables already in the DB")
    ap.add_argument("--init-only", action="store_true", help="only create schema/views")
    ap.add_argument("--set", nargs="*", default=[], metavar="K=V", help="override model parameters")
    ap.add_argument("--label", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    p, paths = Params.with_overrides(a.set), Paths(a.data_dir)
    init_schema(paths)
    if a.init_only:
        return
    from .extract_load import load_raw          # heavy GIS imports only when needed
    from .transform import run_mcda
    from .publish import publish

    engine = create_engine(sqlalchemy_url())
    lineage = {}
    if not a.skip_raw:
        lineage = load_raw(engine, p, paths)
    res = run_mcda(engine, p, paths)
    lineage["dem"] = {"file": str(paths.dem), "bytes": paths.dem.stat().st_size,
                      "modified": dt.datetime.fromtimestamp(paths.dem.stat().st_mtime).isoformat()}
    publish(engine, p, paths, res, lineage, a.label)


if __name__ == "__main__":
    sys.exit(main())
