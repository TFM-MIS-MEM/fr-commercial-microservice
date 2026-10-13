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
- Factor de ráfaga = MIT / MST. σ_S(p) = MST_max / MST_p y σ_I(p) =
  MIT_max / MIT_p, entre los perfiles de una misma serie y configuración.
- Una confirmación «bajó un escalón» si hay una tasa mayor que el valor final
  con resultados mixtos (alguna aprobada y alguna suspendida): solo la
  confirmación repite una tasa.
- Las corridas hechas sin precondiciones o inválidas no cuentan nunca.

COSTE POR PETICIÓN Y DESCOMPOSICIÓN. 1000/MST en ms es el coste de una petición
en capacidad sostenida de la máquina entera (ocho inferencias a la vez sobre
ocho vCPU), no la latencia de una petición suelta. Con `detectFaces` en la misma
serie y configuración, cada `verifyPair` se descompone en dos detecciones más un
resto (dos extracciones del vector y la comparación). Es aproximada: el coste de
`detectFaces` incluye también su decodificación y su sobrecarga HTTP, de modo
que restar dos detecciones descuenta dos veces una sobrecarga HTTP que
`verifyPair` paga una sola vez, y el resto sale por debajo de lo real.

UMBRAL DE LATENCIA. La distribución del cociente de medianas (último tercio /
primero) de todas las corridas válidas a tasas iguales o inferiores al MST final
de su perfil, pasen o no: el ruido con carga sostenible contra el que se fija
1,5.

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


def steps_down(runs: list[dict], final: float | None) -> list[float]:
    """Tasas por encima del valor final con resultados mixtos: bajadas de escalón."""
    by_rate = defaultdict(list)
    for r in runs:
        by_rate[r["meta"]["rate"]].append(r["verdict"]["pass"])
    return sorted(
        rate
        for rate, v in by_rate.items()
        if final is not None and rate > final and any(v) and not all(v)
    )


def profiles(runs: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in runs:
        groups[(r["serie"], r["profile"], r["config"])].append(r)
    out = []
    for (serie, profile, config), every in sorted(groups.items()):
        group = [r for r in every if r["counts"]]
        mit_runs = [r for r in group if r["meta"]["kind"] == "mit"]
        mst_runs = [r for r in group if r["meta"]["kind"] == "mst"]
        mit, mst = value(mit_runs), value(mst_runs)
        peaks = [r["meta"]["service_after"].get("memory_peak_bytes") for r in group]
        swaps = [r["meta"].get("swap_growth_mb") or 0 for r in every]
        out.append(
            {
                "serie": serie,
                "profile": profile,
                "config": config,
                "runs": group,
                "mit": mit,
                "mst": mst,
                "short": len(mit_runs),
                "long": len(mst_runs),
                "invalid": len(every) - len(group),
                "steps_mit": steps_down(mit_runs, mit[0]),
                "steps_mst": steps_down(mst_runs, mst[0]),
                "swap_growth_max": max(swaps) if swaps else 0,
                "peak": max((p for p in peaks if p), default=None),
            }
        )
    return out


def results_table(table: list[dict]) -> None:
    best_s, best_i = defaultdict(float), defaultdict(float)
    for p in table:
        key = (p["serie"], p["config"])
        if p["mst"][0]:
            best_s[key] = max(best_s[key], p["mst"][0])
        if p["mit"][0]:
            best_i[key] = max(best_i[key], p["mit"][0])
    print(
        "| Serie | Perfil | Config. | MIT (/s) | MST (/s) | Ráfaga | σ_S | σ_I | "
        "Coste (ms/pet.) | Breves / largas | Escalón (MIT · MST) | Inválidas | "
        "Swap (máx. crec.) | Pico de memoria de fr |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for p in table:
        mit, mst, key = p["mit"][0], p["mst"][0], (p["serie"], p["config"])
        burst = f"{mit / mst:.3f}" if mit and mst else "—"
        sigma_s = f"{best_s[key] / mst:.2f}" if mst else "—"
        sigma_i = f"{best_i[key] / mit:.2f}" if mit else "—"
        cost = f"{1000 / mst:.1f}" if mst else "—"
        steps = " · ".join(
            ", ".join(f"desde {r:.2f}" for r in p[k]) or "no" for k in ("steps_mit", "steps_mst")
        )
        mem = f"{p['peak'] / MIB:.0f} MiB" if p["peak"] else "—"
        print(
            f"| {p['serie']} | {p['profile']} | {p['config']} | {fmt(*p['mit'])} | "
            f"{fmt(*p['mst'])} | {burst} | {sigma_s} | {sigma_i} | {cost} | "
            f"{p['short']} / {p['long']} | {steps} | {p['invalid']} | "
            f"{p['swap_growth_max']:+.0f} MB | {mem} |"
        )
    print("\n\\* sin confirmar (no hay 3 de 3 a esa tasa).")


def decomposition(table: list[dict]) -> None:
    for p in table:
        if p["profile"] != "detectFaces" or not p["mst"][0]:
            continue
        detect = 1000 / p["mst"][0]
        pairs = [
            q
            for q in table
            if (q["serie"], q["config"]) == (p["serie"], p["config"])
            and q["profile"].startswith("verifyPair-")
            and q["mst"][0]
        ]
        if not pairs:
            continue
        print(f"\n### Descomposición aproximada de verifyPair · {p['serie']} · {p['config']}\n")
        print(
            "Coste en capacidad sostenida (1000/MST). Aproximada: el coste de detectFaces "
            "incluye su decodificación y su sobrecarga HTTP, y restar dos detecciones "
            "descuenta dos veces una sobrecarga que verifyPair paga una vez.\n"
        )
        print("| Perfil | verifyPair (ms) | 2 × detectFaces (ms) | Resto (ms) | Resto / total |")
        print("|---|---|---|---|---|")
        for q in pairs:
            total = 1000 / q["mst"][0]
            rest = total - 2 * detect
            print(
                f"| {q['profile']} | {total:.1f} | {2 * detect:.1f} | {rest:.1f} | "
                f"{rest / total:.0%} |"
            )


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def threshold(table: list[dict]) -> None:
    print("\n### Umbral de latencia: cocientes a tasas ≤ MST final\n")
    print("| Serie | Perfil | Config. | Corridas | Mín. | Mediana | p90 | Máx. | > 1,5 |")
    print("|---|---|---|---|---|---|---|---|---|")
    pooled = defaultdict(list)
    for p in table:
        if not p["mst"][0]:
            continue
        ratios = [
            r["verdict"]["ratio"]
            for r in p["runs"]
            if r["meta"]["kind"] in ("mit", "mst")
            and r["meta"]["rate"] <= p["mst"][0]
            and r["verdict"]["ratio"] is not None
        ]
        pooled[p["serie"]] += ratios
        over = [x for x in ratios if x > 1.5]
        print(
            f"| {p['serie']} | {p['profile']} | {p['config']} | {len(ratios)} | "
            f"{min(ratios):.2f} | {quantile(ratios, 0.5):.2f} | {quantile(ratios, 0.9):.2f} | "
            f"{max(ratios):.2f} | {len(over)} |"
        )
    for serie, ratios in sorted(pooled.items()):
        over = [x for x in ratios if x > 1.5]
        print(
            f"| {serie} | **todas** | | {len(ratios)} | {min(ratios):.2f} | "
            f"{quantile(ratios, 0.5):.2f} | {quantile(ratios, 0.9):.2f} | {max(ratios):.2f} | "
            f"{len(over)} |"
        )


def low_ratios(runs: list[dict]) -> None:
    low = [r for r in runs if r["counts"] and (r["verdict"]["ratio"] or 1) < 0.8]
    print(f"\n### Cociente de mediana por debajo de 0,8: {len(low)}\n")
    for r in low:
        v = r["verdict"]
        print(
            f"- {r['serie']}/{r['profile']}/{r['config']}/{r['name']}: "
            f"{v['med_first']:.0f}→{v['med_last']:.0f} ms (×{v['ratio']:.2f}), "
            f"{'pasa' if v['pass'] else 'no pasa'}"
        )


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
    table = profiles(runs)
    results_table(table)
    decomposition(table)
    threshold(table)
    low_ratios(runs)
    drift_tables(runs)
    runs_table(runs)


if __name__ == "__main__":
    main()
