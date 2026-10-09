#!/usr/bin/env python3
"""MoST sobre un perfil de fr-commercial (TFM-MIS-MEM/docs#120).

Lanza las corridas, decide la siguiente con `criteria.py` y guarda en
`results/` el resumen crudo de cada una con su `.meta.json`. No calcula ningún
resultado final: eso es `analyze.py`, desde lo guardado.

    python3 load/most.py most  --serie S --target fr --op verifyPair --model xs \
                               --workers 1 --threads 1 --concurrency 8
    python3 load/most.py most  ... --only mit --bracket 5 25 --no-confirm   # criba
    python3 load/most.py most  ... --only mst --mit 12.5                     # MST con MIT dado
    python3 load/most.py drift --serie S ... --rate 11 --window 900 --slices 15

EL PROCEDIMIENTO
- Cada corrida: 30 s de calentamiento al 50 % (descartados) y la ventana medida.
  Ventana breve (MIT) de 60 s; larga (MST) de 600 s. Antes de cada una, drenaje
  (fr por debajo del 10 % de CPU) y descanso de 60 s: la máquina llega fría.
- MIT primero. Sin horquilla, desde 1/s se duplica hasta que una corrida no pasa,
  o se divide entre dos hasta que una pasa. Con horquilla, se miden sus dos
  extremos y se amplía por el lado que haga falta.
- MST después, con la ventana larga, y sin dar nada por supuesto: se miden
  0,8·MIT y MIT; si 0,8·MIT no pasa se baja (×0,8) y si MIT pasa se sube
  (×1,25). Así el MST puede salir mayor que el MIT, y el factor ≥ 1 es una
  medición y no algo que impone el procedimiento.
- Bisección entre el mayor que pasa y el menor que no, hasta (hi − lo)/lo ≤ 5 %.
- Confirmación: el valor vale si pasa 3 de 3. Si falla una, se baja un 5 % y se
  vuelve a confirmar, como mucho dos veces.

PRECONDICIONES (el instrumento se niega a empezar, y se recomprueban antes de
cada corrida): Mac con corriente, sin modo de bajo consumo, y ningún contenedor
en marcha que no sea de este compose.

CORRIDA INVÁLIDA (se repite, hasta dos veces, y no cuenta como fallo de ninguna
restricción): fr se reinicia, cambia el conjunto de workers de uvicorn (uno
muere y el supervisor lo sustituye) u hay un OOM en su cgroup.

Python 3.9, solo biblioteca estándar: es el intérprete del anfitrión.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOAD = Path(__file__).resolve().parent
sys.path.insert(0, str(LOAD))
from criteria import evaluate  # noqa: E402

REPO = LOAD.parent
PROJECT = "fr-load"
COMPOSE = ["docker", "compose", "-f", str(LOAD / "compose.yml")]

WARMUP_S = 30
WINDOW = {"mit": 60, "mst": 600}
REST_S = 60
DRAIN_CPU_MAX = 10.0
PRECISION = 0.05
CONFIRMATIONS = 3
CONFIRM_STEP_DOWN = 0.95
CONFIRM_MAX_STEPS = 2
INVALID_RETRIES = 2
MIN_RATE = 0.01

# El listado de procesos de fr, desde dentro: la imagen no trae `ps`.
PROCS = (
    "import json,os\n"
    "me=os.getpid();out=[]\n"
    "for p in os.listdir('/proc'):\n"
    "  if not p.isdigit() or int(p)==me: continue\n"
    "  try:\n"
    "    st=open('/proc/'+p+'/stat').read()\n"
    "    cmd=open('/proc/'+p+'/cmdline','rb').read().replace(b'\\0',b' ').decode().strip()\n"
    "  except OSError: continue\n"
    "  out.append([int(p),int(st.rsplit(')',1)[1].split()[1]),cmd])\n"
    "print(json.dumps(out))\n"
)


class Abort(SystemExit):
    pass


def sh(cmd: list[str], env: dict | None = None, check: bool = True) -> str:
    done = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if check and done.returncode != 0:
        raise Abort(f"falló: {' '.join(cmd)}\n{done.stdout}\n{done.stderr}")
    return done.stdout


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rnd(rate: float) -> float:
    return max(MIN_RATE, round(rate, 2))


# ------------------------------------------------------------ anfitrión


def host_state() -> dict:
    batt = sh(["pmset", "-g", "batt"], check=False)
    settings = sh(["pmset", "-g"], check=False)
    low = re.search(r"^\s*lowpowermode\s+(\d+)", settings, re.M)
    others = []
    fmt = '{{.Names}}\t{{.Label "com.docker.compose.project"}}'
    for line in sh(["docker", "ps", "--format", fmt]).splitlines():
        name, _, project = line.partition("\t")
        if project != PROJECT:
            others.append(name)
    return {
        "at": now(),
        "ac_power": "AC Power" in batt,
        "battery": batt.strip().splitlines()[-1].strip() if batt.strip() else None,
        "low_power_mode": int(low.group(1)) if low else None,
        "caffeinate": subprocess.run(["pgrep", "-x", "caffeinate"], capture_output=True).returncode
        == 0,
        "uptime": sh(["uptime"]).strip(),
        "other_containers": others,
    }


def preconditions(skip: bool) -> dict:
    state = host_state()
    problems = []
    if not state["ac_power"]:
        problems.append("el Mac va con batería")
    if state["low_power_mode"] != 0:
        problems.append(f"modo de bajo consumo: {state['low_power_mode']}")
    if state["other_containers"]:
        problems.append(f"contenedores fuera de load/: {', '.join(state['other_containers'])}")
    state["problems"] = problems
    state["skipped"] = skip
    if problems and not skip:
        raise Abort("Precondiciones incumplidas, no se mide: " + "; ".join(problems))
    return state


# ------------------------------------------------------------ servicio


class Service:
    def __init__(self, target: str, workers: int, threads: int, concurrency: int):
        self.target = target
        self.config = {"workers": workers, "threads": threads, "concurrency": concurrency}
        self.env = dict(
            os.environ,
            EMBEDDING_TOKEN_SECRET=secrets.token_hex(32),
            WEB_CONCURRENCY=str(workers),
            TORCH_NUM_THREADS=str(threads),
            INFERENCE_CONCURRENCY=str(concurrency),
        )

    @property
    def tag(self) -> str:
        if self.target == "calib":
            return "calib"
        c = self.config
        return f"w{c['workers']}t{c['threads']}c{c['concurrency']}"

    def compose(self, *args: str, check: bool = True) -> str:
        return sh([*COMPOSE, *args], env=self.env, check=check)

    def up(self) -> dict:
        services = ["fr"] if self.target == "fr" else ["calib"]
        if self.target == "fr":
            self.compose("build", "fr")
        self.compose("up", "-d", "--wait", "--force-recreate", *services)
        state = self.state()
        if self.target == "fr":
            expected = self.config["workers"] if self.config["workers"] > 1 else 0
            if len(state["workers"]) != expected:
                raise Abort(f"fr arrancó {len(state['workers'])} workers, se esperaban {expected}")
        return state

    def down(self) -> None:
        self.compose("--profile", "tools", "down", "--remove-orphans", check=False)

    def cid(self) -> str:
        return self.compose("ps", "-q", self.target).strip()

    def state(self) -> dict:
        cid = self.cid()
        inspect = json.loads(sh(["docker", "inspect", cid]))[0]
        out = {
            "container": cid[:12],
            "image": inspect["Image"],
            "started_at": inspect["State"]["StartedAt"],
            "restart_count": inspect["RestartCount"],
            "running": inspect["State"]["Running"],
            "oom_killed": inspect["State"]["OOMKilled"],
        }
        if self.target != "fr":
            return out
        procs = json.loads(sh(["docker", "exec", cid, "python", "-c", PROCS]))
        # Con WEB_CONCURRENCY > 1, PID 1 es el supervisor y los workers son sus
        # hijos de `multiprocessing.spawn`; con 1, PID 1 atiende directamente.
        out["processes"] = procs
        out["workers"] = sorted(
            p
            for p, ppid, cmd in procs
            if ppid == 1 and "multiprocessing" in cmd and "resource_tracker" not in cmd
        )
        cg = sh(
            [
                "docker",
                "exec",
                cid,
                "cat",
                "/sys/fs/cgroup/memory.events",
                "/sys/fs/cgroup/memory.peak",
                "/sys/fs/cgroup/memory.current",
            ]
        )
        lines = cg.split("\n")
        events = dict(line.split() for line in lines if len(line.split()) == 2)
        numbers = [line for line in lines if line.strip().isdigit()]
        out["oom_kill"] = int(events.get("oom_kill", 0))
        out["memory_peak_bytes"] = int(numbers[0]) if numbers else None
        out["memory_current_bytes"] = int(numbers[1]) if len(numbers) > 1 else None
        return out

    def cpu(self) -> float:
        raw = sh(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", self.cid()])
        return float(raw.strip().rstrip("%") or 0)

    def drain(self) -> None:
        """Hasta que el servicio deja de trabajar, y luego el descanso."""
        calm = 0
        deadline = time.time() + 600
        while calm < 2:
            calm = calm + 1 if self.cpu() < DRAIN_CPU_MAX else 0
            if time.time() > deadline:
                raise Abort("fr no se queda en reposo en 10 minutos")
        time.sleep(REST_S)


def invalid_reason(before: dict, after: dict) -> str | None:
    if not after["running"]:
        return "el contenedor no está en marcha"
    if (after["started_at"], after["restart_count"]) != (
        before["started_at"],
        before["restart_count"],
    ):
        return "el contenedor se reinició"
    if after["oom_killed"]:
        return "OOMKilled"
    if after.get("oom_kill", 0) != before.get("oom_kill", 0):
        return "OOM en el cgroup de fr"
    if after.get("workers") != before.get("workers"):
        return f"cambió el conjunto de workers: {before.get('workers')} → {after.get('workers')}"
    return None


# ------------------------------------------------------------ corridas


class Runner:
    def __init__(self, args: argparse.Namespace, service: Service):
        self.args = args
        self.service = service
        # Bajo load/: es lo que el contenedor de k6 monta en /load.
        results = Path(args.results).resolve()
        if LOAD not in results.parents and results != LOAD:
            raise Abort(f"--results tiene que estar dentro de {LOAD}")
        self.dir = results / args.serie / profile_name(args) / service.tag
        self.dir.mkdir(parents=True, exist_ok=True)
        self.commit = sh(["git", "-C", str(REPO), "rev-parse", "HEAD"]).strip()
        # Lo que entra en la imagen o en el instrumento; los resultados, no.
        self.dirty = bool(
            sh(
                [
                    "git",
                    "-C",
                    str(REPO),
                    "status",
                    "--porcelain",
                    "--",
                    "src",
                    "Dockerfile",
                    "requirements.txt",
                    "openapi",
                    "load",
                    ":!load/results",
                ]
            ).strip()
        )
        images = [*COMPOSE, "--profile", "tools", "config", "--images"]
        self.images = sh(images, env=service.env).split()

    def seq(self) -> int:
        return len(list(self.dir.glob("*.meta.json"))) + 1

    def run(self, kind: str, rate: float, window: int | None = None, slices: int = 3) -> bool:
        """Una corrida VÁLIDA a `rate`: devuelve si pasa las tres restricciones."""
        window = window or WINDOW[kind]
        for attempt in range(INVALID_RETRIES + 1):
            host_before = preconditions(self.args.skip_preconditions)
            self.service.drain()
            stem = f"{self.seq():03d}-{kind}-{rate:.2f}"
            before = self.service.state()
            started = now()
            out = self.dir / f"{stem}.json"
            done = subprocess.run(
                [
                    *COMPOSE,
                    "--profile",
                    "tools",
                    "run",
                    "--rm",
                    "--no-deps",
                    "k6",
                    "run",
                    "--quiet",
                    "-e",
                    f"TARGET={self.service.target}",
                    "-e",
                    f"OP={self.args.op}",
                    "-e",
                    f"MODEL={self.args.model}",
                    "-e",
                    f"RATE={rate}",
                    "-e",
                    f"WINDOW={window}",
                    "-e",
                    f"WARMUP={WARMUP_S}",
                    "-e",
                    f"SLICES={slices}",
                    "-e",
                    f"OUT=/load/{out.relative_to(LOAD)}",
                    "/load/k6/profile.js",
                ],
                capture_output=True,
                text=True,
                env=self.service.env,
            )
            finished = now()
            after = self.service.state()
            host_after = host_state()
            reason = invalid_reason(before, after)
            if done.returncode != 0 or not out.exists():
                reason = reason or f"k6 terminó con {done.returncode}: {done.stderr[-500:]}"
            verdict = evaluate(json.loads(out.read_text())) if out.exists() else None
            meta = {
                "kind": kind,
                "rate": rate,
                "window": window,
                "slices": slices,
                "profile": profile_name(self.args),
                "target": self.service.target,
                "config": self.service.config,
                "attempt": attempt + 1,
                "valid": reason is None,
                "invalid_reason": reason,
                "verdict": verdict,
                "started": started,
                "finished": finished,
                "commit": self.commit,
                "worktree_dirty": self.dirty,
                "images": self.images,
                "cpuset": {"fr": "2-9", "calib": "2-9", "k6": "0-1", "mongo": "0-1"},
                "host_before": host_before,
                "host_after": host_after,
                "service_before": before,
                "service_after": after,
                "k6_stdout": done.stdout.strip(),
            }
            meta_text = json.dumps(meta, indent=1, ensure_ascii=False)
            (self.dir / f"{stem}.meta.json").write_text(meta_text)
            line = f"{stem}: " + (
                "INVÁLIDA, " + reason
                if reason
                else ("pasa" if verdict["pass"] else "no pasa")
                + f" · éxito {verdict['success']:.3f} · descartadas {verdict['dropped']}"
                f" · en vuelo {verdict['inflight_max']}/{verdict['max_vus']}"
                f" · mediana {verdict['med_first']:.0f}→{verdict['med_last']:.0f} ms"
                f" (×{verdict['ratio']:.2f})"
            )
            print(line, flush=True)
            if reason is None:
                return verdict["pass"]
            if after["running"] is False or after["restart_count"] != before["restart_count"]:
                self.service.up()
        raise Abort(f"{INVALID_RETRIES + 1} corridas inválidas seguidas a {rate}/s")

    # ------------------------------------------------------------ búsqueda

    def search(
        self, kind: str, lo: float | None, hi: float | None, start: float, down: float, up: float
    ) -> tuple[float, float]:
        """El mayor que pasa y el menor que no, a menos de PRECISION."""
        if lo is not None and hi is not None:
            passed, failed = self._bracket(kind, lo, hi, down, up)
        else:
            passed, failed = self._probe(kind, start, down, up)
        while (failed - passed) / passed > PRECISION:
            mid = rnd((passed + failed) / 2)
            if mid in (passed, failed):
                break
            if self.run(kind, mid):
                passed = mid
            else:
                failed = mid
        return passed, failed

    def _step_down(self, kind: str, r: float, down: float) -> tuple[float, float]:
        """Desde `r`, que no pasa, baja hasta la primera que pasa."""
        failed = r
        while True:
            r = rnd(r * down)
            if r <= MIN_RATE:
                raise Abort("ni la tasa mínima pasa")
            if self.run(kind, r):
                return r, failed
            failed = r

    def _step_up(self, kind: str, r: float, up: float) -> tuple[float, float]:
        """Desde `r`, que pasa, sube hasta la primera que no pasa."""
        while self.run(kind, rnd(r * up)):
            r = rnd(r * up)
        return r, rnd(r * up)

    def _bracket(
        self, kind: str, lo: float, hi: float, down: float, up: float
    ) -> tuple[float, float]:
        """Mide los dos extremos y amplía por el lado que haga falta."""
        lo = rnd(lo)
        if not self.run(kind, lo):
            return self._step_down(kind, lo, down)
        hi = rnd(hi)
        if self.run(kind, hi):
            return self._step_up(kind, hi, up)
        return lo, hi

    def _probe(self, kind: str, start: float, down: float, up: float) -> tuple[float, float]:
        """Sin horquilla: duplica si pasa, divide si no."""
        r = rnd(start)
        if self.run(kind, r):
            return self._step_up(kind, r, up)
        return self._step_down(kind, r, down)

    def confirm(self, kind: str, rate: float) -> float:
        """3 de 3 a `rate`, contando la que ya pasó en la búsqueda."""
        have = 1
        for _ in range(CONFIRM_MAX_STEPS + 1):
            ok = True
            for _ in range(CONFIRMATIONS - have):
                if not self.run(kind, rate):
                    ok = False
                    break
            if ok:
                return rate
            rate, have = rnd(rate * CONFIRM_STEP_DOWN), 0
        raise Abort(f"{kind}: no se confirma tras {CONFIRM_MAX_STEPS} bajadas del 5 %")


def profile_name(args: argparse.Namespace) -> str:
    if args.target == "calib":
        return "calibration"
    return f"{args.op}-{args.model}" if args.op == "verifyPair" else args.op


def cmd_most(args: argparse.Namespace, runner: Runner) -> None:
    mit = args.mit
    if args.only in (None, "mit"):
        lo, hi = args.bracket or (None, None)
        mit, _ = runner.search("mit", lo, hi, start=1.0, down=0.5, up=2.0)
        if not args.no_confirm:
            mit = runner.confirm("mit", mit)
        print(f"MIT = {mit}/s", flush=True)
    if args.only in (None, "mst"):
        if mit is None:
            raise Abort("--only mst exige --mit")
        mst, _ = runner.search("mst", 0.8 * mit, mit, start=mit, down=0.8, up=1.25)
        if not args.no_confirm:
            mst = runner.confirm("mst", mst)
        print(f"MST = {mst}/s · ráfaga = {mit / mst:.3f}", flush=True)


def cmd_drift(args: argparse.Namespace, runner: Runner) -> None:
    runner.run("drift", args.rate, window=args.window, slices=args.slices)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("most", "drift"):
        p = sub.add_parser(name)
        p.add_argument("--serie", required=True)
        p.add_argument("--target", choices=["fr", "calib"], default="fr")
        p.add_argument("--op", choices=["verifyPair", "detectFaces"], default="verifyPair")
        p.add_argument("--model", choices=["xs", "s", "base"], default="xs")
        p.add_argument("--workers", type=int, default=1)
        p.add_argument("--threads", type=int, default=1)
        p.add_argument("--concurrency", type=int, default=1)
        p.add_argument("--results", default=str(LOAD / "results"))
        p.add_argument(
            "--skip-preconditions",
            action="store_true",
            help="solo para probar el instrumento; queda escrito en cada .meta.json",
        )
        p.add_argument("--keep", action="store_true", help="no parar el compose al acabar")
        if name == "most":
            p.add_argument("--only", choices=["mit", "mst"])
            p.add_argument("--mit", type=float)
            p.add_argument("--bracket", type=float, nargs=2, metavar=("LO", "HI"))
            p.add_argument("--no-confirm", action="store_true")
        else:
            p.add_argument("--rate", type=float, required=True)
            p.add_argument("--window", type=int, default=900)
            p.add_argument("--slices", type=int, default=15)
    args = parser.parse_args()

    preconditions(args.skip_preconditions)
    service = Service(args.target, args.workers, args.threads, args.concurrency)
    try:
        print(f"{now()} · {profile_name(args)} · {service.tag} · arrancando", flush=True)
        service.up()
        runner = Runner(args, service)
        (cmd_most if args.cmd == "most" else cmd_drift)(args, runner)
    finally:
        if not args.keep:
            service.down()


if __name__ == "__main__":
    main()
