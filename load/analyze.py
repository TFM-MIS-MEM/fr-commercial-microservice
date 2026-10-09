#!/usr/bin/env python3
"""MST, MIT, factor de ráfaga y σ desde los resúmenes guardados en `results/`.

    python3 load/analyze.py                 # todas las series
    python3 load/analyze.py exploration     # una

No lee ningún veredicto de los `.meta.json`: vuelve a evaluar cada resumen crudo
con `criteria.py`. Del `.meta.json` toma solo lo que el resumen no puede saber:
si la corrida fue válida, la configuración y la memoria de fr. Si el veredicto
recalculado no coincide con el guardado, lo dice: las restricciones cambiaron
después de medir.

LAS REGLAS, LAS MISMAS QUE APLICA most.py
- Valor confirmado: la mayor tasa con al menos 3 corridas válidas, todas
  aprobadas.
- Sin confirmar (la criba de la exploración): la mayor tasa aprobada por debajo
  de la menor suspendida. Se marca con «*».
- Factor de ráfaga = MIT / MST. σ_S(p) = MST_max / MST_p, entre los perfiles de
  una misma serie y configuración.
- Las corridas hechas sin precondiciones o inválidas no cuentan nunca.

Python 3.9, solo biblioteca estándar.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

LOAD = Path(__file__).resolve().parent
sys.path.insert(0, str(LOAD))
from criteria import evaluate, slice_stats  # noqa: E402

RESULTS = LOAD / "results"
MIB = 1024 * 1024


def load_runs(serie: str | None) -> list[dict]:
    runs = []
    for meta_path in sorted(RESULTS.glob("*/*/*/*.meta.json")):
        serie_name, profile, config = meta_path.parts[-4:-1]
        if serie and serie_name != serie:
            continue
        meta = json.loads(meta_path.read_text())
        summary_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".json"))
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else None
        verdict = evaluate(summary) if summary else None
        runs.append(
            {
                "serie": serie_name,
                "profile": profile,
                "config": config,
                "name": meta_path.name.replace(".meta.json", ""),
                "meta": meta,
                "summary": summary,
                "verdict": verdict,
                "counts": meta["valid"] and not meta["host_before"].get("skipped") and verdict,
                "drifted": bool(
                    verdict and meta.get("verdict") and verdict["pass"] != meta["verdict"]["pass"]
                ),
            }
        )
    return runs


def value(runs: list[dict]) -> tuple[float | None, bool]:
    """(tasa, confirmada) según las reglas de la cabecera."""
    by_rate = defaultdict(list)
    for r in runs:
        by_rate[r["meta"]["rate"]].append(r["verdict"]["pass"])
    confirmed = [rate for rate, v in by_rate.items() if len(v) >= 3 and all(v)]
    if confirmed:
        return max(confirmed), True
    failing = [rate for rate, v in by_rate.items() if not all(v)]
    ceiling = min(failing) if failing else float("inf")
    passing = [rate for rate, v in by_rate.items() if all(v) and rate < ceiling]
    return (max(passing), False) if passing else (None, False)


def fmt(rate: float | None, confirmed: bool) -> str:
    return "—" if rate is None else f"{rate:.2f}{'' if confirmed else '*'}"


def warnings(runs: list[dict]) -> None:
    for r in runs:
        if r["drifted"]:
            print(
                f"AVISO: {r['serie']}/{r['profile']}/{r['config']}/{r['name']}: el veredicto "
                "recalculado no coincide con el guardado."
            )
    ignored = [r for r in runs if not r["counts"]]
    if ignored:
        print(f"{len(ignored)} corridas no cuentan (inválidas o sin precondiciones).\n")


def results_table(runs: list[dict]) -> None:
    groups = defaultdict(list)
    for r in runs:
        if r["counts"]:
            groups[(r["serie"], r["profile"], r["config"])].append(r)

    print(
        "| Serie | Perfil | Configuración | MIT (/s) | MST (/s) | Ráfaga | σ | "
        "Corridas | Pico de memoria de fr |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    table = []
    for (serie, profile, config), group in sorted(groups.items()):
        mit = value([r for r in group if r["meta"]["kind"] == "mit"])
        mst = value([r for r in group if r["meta"]["kind"] == "mst"])
        peaks = [r["meta"]["service_after"].get("memory_peak_bytes") for r in group]
        peaks = [p for p in peaks if p]
        table.append((serie, profile, config, mit, mst, len(group), max(peaks) if peaks else None))
    best = defaultdict(float)
    for serie, _, config, _, mst, *_ in table:
        if mst[0]:
            best[(serie, config)] = max(best[(serie, config)], mst[0])
    for serie, profile, config, mit, mst, n, peak in table:
        burst = f"{mit[0] / mst[0]:.3f}" if mit[0] and mst[0] else "—"
        sigma = f"{best[(serie, config)] / mst[0]:.2f}" if mst[0] else "—"
        mem = f"{peak / MIB:.0f} MiB" if peak else "—"
        print(
            f"| {serie} | {profile} | {config} | {fmt(*mit)} | {fmt(*mst)} | {burst} | {sigma} "
            f"| {n} | {mem} |"
        )
    print("\n\\* sin confirmar (no hay 3 de 3 a esa tasa).")


def drift_tables(runs: list[dict]) -> None:
    drifts = [r for r in runs if r["counts"] and r["meta"]["kind"] == "drift"]
    for r in drifts:
        v = r["verdict"]
        print(
            f"\n### Deriva · {r['serie']}/{r['profile']}/{r['config']} · {v['rate']}/s · "
            f"{v['window']} s · éxito {v['success']:.4f} · descartadas {v['dropped']}\n"
        )
        print("| Tramo | Peticiones | Mediana (ms) | p95 (ms) | En vuelo (máx.) |")
        print("|---|---|---|---|---|")
        for s in slice_stats(r["summary"]):
            print(
                f"| {s['slice']} | {s['count']} | {s['med']:.0f} | {s['p95']:.0f} "
                f"| {s['inflight_max']:.0f} |"
            )


def runs_table(runs: list[dict]) -> None:
    print("\n### Corridas\n")
    print(
        "| Serie | Perfil | Config. | Corrida | Válida | Pasa | Éxito | Descartadas | "
        "En vuelo | Mediana t1→t3 (ms) | Ratio |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in runs:
        v = r["verdict"]
        if not v:
            print(
                f"| {r['serie']} | {r['profile']} | {r['config']} | {r['name']} | no "
                f"({r['meta']['invalid_reason']}) | | | | | | |"
            )
            continue
        why = r["meta"]["invalid_reason"] or "sin precondiciones"
        valid = "sí" if r["counts"] else f"no ({why})"
        med = (
            f"{v['med_first']:.0f}→{v['med_last']:.0f}"
            if v["med_first"] is not None and v["med_last"] is not None
            else "—"
        )
        ratio = f"{v['ratio']:.2f}" if v["ratio"] is not None else "—"
        print(
            f"| {r['serie']} | {r['profile']} | {r['config']} | {r['name']} | {valid} | "
            f"{'sí' if v['pass'] else 'no'} | {v['success']:.3f} | {v['dropped']} | "
            f"{v['inflight_max']:.0f}/{v['max_vus']} | {med} | {ratio} |"
        )


def main() -> None:
    runs = load_runs(sys.argv[1] if len(sys.argv) > 1 else None)
    if not runs:
        print("No hay corridas.")
        return
    warnings(runs)
    results_table(runs)
    drift_tables(runs)
    runs_table(runs)


if __name__ == "__main__":
    main()
