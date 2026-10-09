"""Las tres restricciones de calidad de MoST sobre el resumen de una corrida.

Única definición: la usan `most.py`, para decidir el paso siguiente de la
búsqueda, y `analyze.py`, que vuelve a evaluar desde los resúmenes guardados.
Las mismas tres en la ventana larga y en la breve (capítulo 7).

1. ÉXITO. Más del 95 % de respuestas 2xx en la ventana medida.
2. PENDIENTES. Ninguna iteración descartada y nunca todos los VUs ocupados a la
   vez: con la tasa de llegada fija, una cola que crece agota los VUs y k6
   empieza a descartar.
3. LATENCIA. La MEDIANA del último tercio no supera 1,5 veces la del primero.
   Mediana y no p95: con carga sostenible, el p95 por tercios oscila mucho más
   que la mediana (el umbral y su justificación, con la deriva medida, en
   load/README.md). Una cola que crece no pasa: con una sobrecarga del 1 %, el
   ratio sale por encima de 3 incluso en 60 s.

Python 3.9, solo biblioteca estándar: es el intérprete del anfitrión.
"""

from __future__ import annotations

SUCCESS_MIN = 0.95
LATENCY_RATIO_MAX = 1.5


def _values(metrics: dict, name: str) -> dict | None:
    metric = metrics.get(name)
    return metric["values"] if metric else None


def slice_stats(summary: dict) -> list[dict]:
    """Mediana, p95, peticiones y en vuelo de cada tramo de la ventana medida."""
    metrics = summary["k6"]["metrics"]
    out = []
    for s in range(1, summary["params"]["SLICES"] + 1):
        duration = _values(metrics, f"http_req_duration{{scenario:measure,slice:{s}}}") or {}
        inflight = _values(metrics, f"inflight{{scenario:measure,slice:{s}}}") or {}
        out.append(
            {
                "slice": s,
                "count": duration.get("count", 0),
                "med": duration.get("med"),
                "p95": duration.get("p(95)"),
                "inflight_max": inflight.get("max"),
            }
        )
    return out


def evaluate(summary: dict) -> dict:
    """Veredicto de las tres restricciones, con las cifras que lo sostienen."""
    params = summary["params"]
    metrics = summary["k6"]["metrics"]

    ok = _values(metrics, "ok_2xx{scenario:measure}")
    success = ok["rate"] if ok else 0.0
    requests = (ok["passes"] + ok["fails"]) if ok else 0

    dropped = (_values(metrics, "dropped_iterations{scenario:measure}") or {}).get("count", 0)
    inflight_max = (_values(metrics, "inflight{scenario:measure}") or {}).get("max", 0)

    med_first = (_values(metrics, "http_req_duration{scenario:measure,third:1}") or {}).get("med")
    med_last = (_values(metrics, "http_req_duration{scenario:measure,third:3}") or {}).get("med")
    ratio = (med_last / med_first) if (med_first and med_last is not None) else None

    checks = {
        "success": requests > 0 and success > SUCCESS_MIN,
        "pending": dropped == 0 and inflight_max < params["MAX_VUS"],
        "latency": ratio is not None and ratio <= LATENCY_RATIO_MAX,
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "rate": params["RATE"],
        "window": params["WINDOW"],
        "requests": requests,
        "success": success,
        "dropped": dropped,
        "inflight_max": inflight_max,
        "max_vus": params["MAX_VUS"],
        "med_first": med_first,
        "med_last": med_last,
        "ratio": ratio,
    }
