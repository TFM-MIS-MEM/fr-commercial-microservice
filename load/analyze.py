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
- Factor de ráfaga = MIT / MST. σ_S(p) = MST_ref / MST_p y σ_I(p) =
  MIT_ref / MIT_p, entre los perfiles de una misma serie y configuración, con
  `detectFaces` como referencia si está en ella y, si no, el mayor.
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

1:N (TFM-MIS-MEM/docs#62). Con los perfiles `searchGallery-xs-N`:
- f(n): la mediana de latencia de las corridas a 1, 2 y 4/s, y 1000/MST, por
  tamaño, con su incremento sobre el menor y el ajuste lineal frente a n. La
  f(n) en latencia se toma de 1 y 2/s: a 4/s la latencia baja en los tres
  tamaños (hipótesis no medida: la frecuencia de la CPU con poca carga).
- Por corrida: vCPU de fr y de mongo (Δusage_usec / Δsampled_at), la de mongo
  menos la de su corrida a 1/s (el fondo), la latencia media de lectura de mongo
  (Δlatency / Δops) y el swap antes y después.
- El criterio de «lo limita MongoDB» de README.md, al pie de la letra. No fija
  cifra para «claramente», así que el veredicto se da en función del umbral de
  la subida de latencia, con el margen de la cláusula de CPU.
- La cota de MongoDB con lo medido en las MST confirmadas: CPU y tiempo de
  lectura por búsqueda.

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
    # Detección como referencia, si está en la serie y la configuración.
    for p in table:
        if p["profile"] == "detectFaces" and p["mst"][0] and p["mit"][0]:
            best_s[(p["serie"], p["config"])] = p["mst"][0]
            best_i[(p["serie"], p["config"])] = p["mit"][0]
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


# ------------------------------------------------------------ 1:N

SEARCH = "searchGallery-"
#: cpuset de mongo: los vCPU 0–1, que comparte con k6 (compose.yml).
MONGO_VCPU = 2
#: Inferencias concurrentes de la configuración de la campaña (w1t1c8).
IN_FLIGHT = 8
#: El umbral de latencia del propio instrumento (criteria.py), solo como punto
#: de lectura del veredicto, no como cifra de «claramente».
READ_RATIO_MARK = 1.5


def faces(profile: str) -> int:
    return int(profile.rsplit("-", 1)[1])


def search_profiles(table: list[dict]) -> list[dict]:
    return sorted(
        (p for p in table if p["profile"].startswith(SEARCH)), key=lambda p: faces(p["profile"])
    )


def vcpu(before: dict, after: dict) -> float:
    return (
        (after["usage_usec"] - before["usage_usec"])
        / (after["sampled_at"] - before["sampled_at"])
        / 1e6
    )


def resources(r: dict) -> dict | None:
    """vCPU de fr y de mongo, lectura media de mongo y swap de una corrida."""
    b, a = r["meta"]["service_before"], r["meta"]["service_after"]
    if "cpu" not in b or "cpu" not in b.get("mongo", {}):
        return None
    r0, r1 = b["mongo"]["op_latencies_reads"], a["mongo"]["op_latencies_reads"]
    ops = r1["ops"] - r0["ops"]
    return {
        "fr": vcpu(b["cpu"], a["cpu"]),
        "mongo": vcpu(b["mongo"]["cpu"], a["mongo"]["cpu"]),
        "read_ms": (r1["latency"] - r0["latency"]) / ops / 1000 if ops else None,
        "reads": ops,
        "cpu_s": (a["mongo"]["cpu"]["usage_usec"] - b["mongo"]["cpu"]["usage_usec"]) / 1e6,
        "elapsed": a["mongo"]["cpu"]["sampled_at"] - b["mongo"]["cpu"]["sampled_at"],
        "read_s": (r1["latency"] - r0["latency"]) / 1e6,
        "swap": (r["meta"]["host_before"]["swap_used_mb"], r["meta"]["host_after"]["swap_used_mb"]),
    }


def median_latency(r: dict) -> float:
    return r["summary"]["k6"]["metrics"]["http_req_duration{scenario:measure}"]["values"]["med"]


def at_rate(p: dict, rate: float) -> dict | None:
    """La primera corrida breve válida a esa tasa."""
    return next(
        (r for r in p["runs"] if r["meta"]["kind"] == "mit" and r["meta"]["rate"] == rate), None
    )


def linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """(ordenada, pendiente, R²) por mínimos cuadrados."""
    n = range(len(xs))
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in n) / sum((x - mx) ** 2 for x in xs)
    icpt = my - slope * mx
    total = sum((y - my) ** 2 for y in ys)
    resid = sum((ys[i] - icpt - slope * xs[i]) ** 2 for i in n)
    return icpt, slope, 1 - resid / total if total else 1.0


def fn_table(table: list[dict]) -> None:
    sizes = [p for p in search_profiles(table) if p["mst"][0]]
    if not sizes:
        return
    rows = []
    for p in sizes:
        lat = {rate: at_rate(p, rate) for rate in (1.0, 2.0, 4.0)}
        lat = {rate: median_latency(r) if r else None for rate, r in lat.items()}
        both = [lat[1.0], lat[2.0]]
        rows.append(
            {
                "n": faces(p["profile"]),
                "lat": lat,
                "lat12": sum(both) / 2 if None not in both else None,
                "cost": 1000 / p["mst"][0],
            }
        )
    first = rows[0]
    where = f"{sizes[0]['serie']} · {sizes[0]['config']}"
    print(f"\n### f(n): búsqueda 1:N por tamaño de galería · {where}\n")
    print(
        "Latencia: mediana de la ventana medida de las corridas a 1, 2 y 4/s; la f(n) en "
        "latencia es la media de 1 y 2/s. Capacidad: 1000/MST. Incrementos sobre "
        f"{first['n']} rostros.\n"
    )
    print(
        "| Rostros | 1/s (ms) | 2/s (ms) | 4/s (ms) | Latencia 1–2/s (ms) | Δ latencia "
        "(1/s · 2/s · 4/s) | 1000/MST (ms) | Δ 1000/MST |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for row in rows:
        lat = row["lat"]
        deltas = " · ".join(
            f"{lat[k] - first['lat'][k]:+.1f}"
            if lat[k] is not None and first["lat"][k] is not None
            else "—"
            for k in (1.0, 2.0, 4.0)
        )
        cells = " | ".join(f"{lat[k]:.1f}" if lat[k] is not None else "—" for k in (1.0, 2.0, 4.0))
        lat12 = f"{row['lat12']:.1f}" if row["lat12"] is not None else "—"
        print(
            f"| {row['n']} | {cells} | {lat12} | {deltas} | {row['cost']:.2f} | "
            f"{row['cost'] - first['cost']:+.2f} |"
        )
    if len(rows) < 2:
        return
    ns = [row["n"] for row in rows]
    icpt, slope, r2 = linear_fit(ns, [row["cost"] for row in rows])
    resid = ", ".join(f"{row['cost'] - icpt - slope * row['n']:+.2f}" for row in rows)
    print(
        f"\nAjuste lineal de 1000/MST frente a n: {icpt:.2f} ms + **{slope * 1000:.3f} µs por "
        f"rostro** · R² {r2:.4f} · residuos {resid} ms ({len(rows)} puntos)."
    )
    if all(row["lat12"] is not None for row in rows):
        icpt, slope, r2 = linear_fit(ns, [row["lat12"] for row in rows])
        print(
            f"Ajuste lineal de la latencia a 1–2/s frente a n: {icpt:.2f} ms + "
            f"**{slope * 1000:.3f} µs por rostro** · R² {r2:.4f}."
        )


def baseline(p: dict) -> dict | None:
    """Los recursos de la corrida a 1/s del perfil: el fondo."""
    r = at_rate(p, 1.0)
    return resources(r) if r else None


def resources_table(table: list[dict]) -> None:
    sizes = search_profiles(table)
    if not sizes:
        return
    print("\n### Recursos por corrida, 1:N\n")
    print(
        "vCPU media entre `service_before` y `service_after`: calentamiento al 50 % y "
        "ventana. El fondo de mongo es su vCPU en la corrida a 1/s del mismo perfil.\n"
    )
    print(
        "| Perfil | Corrida | Pasa | fr (vCPU) | mongo (vCPU) | mongo − fondo | "
        "Lectura media de mongo (ms) | Swap antes → después (MB) |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for p in sizes:
        base = baseline(p)
        for r in p["runs"]:
            res = resources(r)
            if res is None:
                continue
            net = f"{res['mongo'] - base['mongo']:.3f}" if base else "—"
            read = f"{res['read_ms']:.2f}" if res["read_ms"] is not None else "—"
            print(
                f"| {p['profile']} | {r['name']} | {'sí' if r['verdict']['pass'] else 'no'} | "
                f"{res['fr']:.2f} | {res['mongo']:.3f} | {net} | {read} | "
                f"{res['swap'][0]:.0f} → {res['swap'][1]:.0f} |"
            )


def confirmed_mst_runs(p: dict) -> list[dict]:
    return [
        r
        for r in p["runs"]
        if r["meta"]["kind"] == "mst" and r["meta"]["rate"] == p["mst"][0] and r["verdict"]["pass"]
    ]


def criterion(table: list[dict]) -> None:
    sizes = [p for p in search_profiles(table) if p["mst"][0]]
    if not sizes:
        return
    control = sizes[0]
    reference_runs = [resources(r) for r in confirmed_mst_runs(control)]
    reference_runs = [x for x in reference_runs if x]
    if not reference_runs:
        return
    ref = sum(x["fr"] for x in reference_runs) / len(reference_runs)
    print("\n### El criterio de «lo limita MongoDB», al pie de la letra\n")
    print(
        "En las corridas que fallan: (a) la lectura media de mongo sube «claramente» sobre "
        "la de la corrida a 1/s del mismo perfil, y (b) la CPU de fr queda por debajo de la "
        f"que alcanzó en el MST de {control['profile']}: **{ref:.3f} vCPU**, media de sus "
        f"{len(reference_runs)} corridas confirmadas. El criterio no fija cifra para "
        f"«claramente»: el veredicto se da en función del umbral u. {control['profile']} es "
        "el control, y en él (b) es falsa por construcción.\n"
    )
    print("| Perfil | Corrida | Lectura / la de 1/s | fr (vCPU) | (b) fr < referencia |")
    print("|---|---|---|---|---|")
    summary = []
    for p in sizes:
        base = baseline(p)
        failing = []
        for r in p["runs"]:
            res = resources(r)
            if r["verdict"]["pass"] or res is None or not base or not res["read_ms"]:
                continue
            ratio = res["read_ms"] / base["read_ms"]
            failing.append((ratio, res["fr"]))
            print(
                f"| {p['profile']} | {r['name']} | ×{ratio:.2f} | {res['fr']:.2f} | "
                f"{'sí' if res['fr'] < ref else 'no'} |"
            )
        summary.append((p, failing))
    print()
    for p, failing in summary:
        m = len(failing)
        holds = sorted(ratio for ratio, fr in failing if fr < ref)
        margin = ref - max(fr for _, fr in failing) if failing else None
        if not holds:
            print(
                f"- **{p['profile']}**: MongoDB en 0 de {m} para cualquier u; (b) no se "
                f"cumple en ninguna (margen {margin:+.3f} vCPU)."
            )
            continue
        at_mark = sum(1 for ratio in holds if ratio >= READ_RATIO_MARK)
        print(
            f"- **{p['profile']}**: MongoDB en {len(holds)} de {m} para u ≤ ×{holds[0]:.2f}; "
            f"en {at_mark} de {m} a ×{READ_RATIO_MARK:.1f}; en 0 para u > ×{holds[-1]:.2f}. "
            f"Margen de (b): {margin:+.3f} vCPU."
        )


def mongo_bound(table: list[dict]) -> None:
    sizes = [p for p in search_profiles(table) if p["mst"][0]]
    if not sizes:
        return
    print("\n### Cota de MongoDB, con lo medido en las MST confirmadas\n")
    print(
        "Por búsqueda: CPU de mongo menos su fondo (la corrida a 1/s) y tiempo de lectura "
        "acumulado, sobre las búsquedas de la corrida (tasa × (ventana + 0,5 × "
        f"calentamiento)). Cotas: {MONGO_VCPU} vCPU / CPU por búsqueda (su cpuset) y "
        f"{IN_FLIGHT} en vuelo / lectura por búsqueda.\n"
    )
    print(
        "| Perfil | MST (/s) | Lecturas por búsqueda | Lectura por búsqueda (ms) | "
        "CPU de mongo por búsqueda (ms) | Cota por CPU (/s) | Cota con 8 en vuelo (/s) |"
    )
    print("|---|---|---|---|---|---|---|")
    for p in sizes:
        base = baseline(p)
        for r in confirmed_mst_runs(p):
            res = resources(r)
            if not res or not base:
                continue
            params = r["summary"]["params"]
            searches = params["RATE"] * (params["WINDOW"] + 0.5 * params["WARMUP"])
            cpu = (res["cpu_s"] - base["mongo"] * res["elapsed"]) / searches
            read = res["read_s"] / searches
            print(
                f"| {p['profile']} | {p['mst'][0]:.2f} | {res['reads'] / searches:.2f} | "
                f"{read * 1000:.1f} | {cpu * 1000:.1f} | {MONGO_VCPU / cpu:.0f} | "
                f"{IN_FLIGHT / read:.0f} |"
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
    fn_table(table)
    criterion(table)
    mongo_bound(table)
    resources_table(table)
    threshold(table)
    low_ratios(runs)
    drift_tables(runs)
    runs_table(runs)


if __name__ == "__main__":
    main()
