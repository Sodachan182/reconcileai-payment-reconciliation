"""Performance + accuracy benchmark on generated data (writes docs/benchmark_results.json).

    .venv\\Scripts\\python.exe scripts\\benchmark.py [--sizes 300 1000 5000] [--seeds 5]

Timings are wall-clock on the machine that runs it; nothing here is estimated.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reconcileai.config import Settings  # noqa: E402
from reconcileai.demo_data import generate, write_dataset  # noqa: E402
from reconcileai.evaluation import evaluate  # noqa: E402
from reconcileai.ingestion.column_mapper import suggest_mapping  # noqa: E402
from reconcileai.ingestion.normalizer import normalize_bank, normalize_orders  # noqa: E402
from reconcileai.ingestion.readers import read_path  # noqa: E402
from reconcileai.matching.engine import run_matching  # noqa: E402
from reconcileai.reconciliation.service import reconcile  # noqa: E402
from reconcileai.reports.exporters import export_xlsx  # noqa: E402
from reconcileai.reports.tables import build_tables  # noqa: E402


def run_size(n: int, seed: int, tmp: Path) -> dict:
    s = Settings()
    ds = generate(n, seed, challenging=True)
    d = tmp / f"n{n}_s{seed}"
    write_dataset(ds, d, d / "truth.json")

    t0 = time.perf_counter()
    to, tb = read_path(d / "orders.xlsx"), read_path(d / "bank_statement.xlsx")
    t_import = time.perf_counter() - t0

    t0 = time.perf_counter()
    orders, _ = normalize_orders(to, suggest_mapping(list(to.frame.columns), "orders"), s)
    txns, _ = normalize_bank(tb, suggest_mapping(list(tb.frame.columns), "bank"), s)
    t_norm = time.perf_counter() - t0

    tracemalloc.start()
    t0 = time.perf_counter()
    match = run_matching(orders, txns, s)
    t_match = time.perf_counter() - t0
    t0 = time.perf_counter()
    rs = reconcile(orders, txns, s, [], match)
    t_rec = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    t0 = time.perf_counter()
    xlsx = export_xlsx(rs, build_tables(rs))
    t_report = time.perf_counter() - t0

    m = evaluate(rs, ds.truth)
    return {
        "orders": len(orders), "bank_lines": len(txns), "seed": seed,
        "seconds": {"read_xlsx": round(t_import, 3), "normalize_validate": round(t_norm, 3),
                    "matching": round(t_match, 3), "allocate_and_invariants": round(t_rec, 3),
                    "xlsx_report": round(t_report, 3)},
        "matching_stage_seconds": {k: round(v, 4) for k, v in match.timings.items()},
        "peak_memory_mb_matching": round(peak / 1e6, 1), "report_bytes": len(xlsx),
        "precision": m["automatic"]["precision"], "recall": m["automatic"]["recall"],
        "incorrect_auto": m["automatic"]["fp"], "review_coverage": m["review_coverage"]["coverage"],
        "integrity_ok": rs.integrity_ok,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[300, 1000, 5000])
    ap.add_argument("--seeds", type=int, default=5, help="Extra seeds at 300 orders for accuracy stability")
    ap.add_argument("--out", default=str(ROOT / "docs" / "benchmark_results.json"))
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="reconcileai_bench_"))
    results = {"machine": {"python": platform.python_version(), "platform": platform.platform(),
                           "processor": platform.processor()}, "sizes": [], "seeds": []}
    for n in args.sizes:
        r = run_size(n, 2026, tmp)
        results["sizes"].append(r)
        print(f"{n:>6} orders / {r['bank_lines']:>5} lines: read {r['seconds']['read_xlsx']:.2f}s, "
              f"normalize {r['seconds']['normalize_validate']:.2f}s, match {r['seconds']['matching']:.2f}s, "
              f"allocate {r['seconds']['allocate_and_invariants']:.2f}s, report {r['seconds']['xlsx_report']:.2f}s, "
              f"peak {r['peak_memory_mb_matching']} MB, precision {r['precision']:.3f}, recall {r['recall']:.3f}")
    for seed in range(1, args.seeds + 1):
        r = run_size(300, seed, tmp)
        results["seeds"].append({k: r[k] for k in ("seed", "precision", "recall", "incorrect_auto", "review_coverage")})
        print(f"seed {seed}: precision {r['precision']:.3f}, recall {r['recall']:.3f}, incorrect {r['incorrect_auto']}")
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
