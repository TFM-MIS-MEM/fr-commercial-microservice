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
    python3 load/most.py chain                                               # la noche
    python3 load/most.py search                                              # los tres 1:N
    python3 load/most.py seed  --faces 50000                                 # solo la siembra

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

LA CADENA NOCTURNA (`chain`): criba de las siete configuraciones → MST de las
dos de mayor MIT → deriva de 15 min a 0,9·MIT de la de mayor MST → MoST completo
de verifyPair xs con ella. La deriva es la puerta: si no cumple las tres
restricciones con tercios de 5 min, la cadena termina sin medir xs. El estado,
en `results/chain-status.json`.

LOS PERFILES 1:N (`search`, TFM-MIS-MEM/docs#62): searchGallery con `xs` y
galerías de 5.000, 20.000 y 50.000 rostros, en secuencia, cada uno con su MoST
completo en `w1t1c8`. Tras levantar el banco, `seed.py` siembra la galería y la
comprueba (bloquea: si falla, el perfil no se mide); al acabar, la vuelve a
comprobar, y si esa falla el perfil queda como no válido. El estado, en
`results/search-status.json`.

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


class PreconditionFailed(Abort):
    """La máquina dejó de estar en condiciones: la cadena entera se detiene."""


class GalleryInvalid(Abort):
    """La comprobación final de la galería falló: el perfil no vale."""

    def __init__(self, reason: str, result: dict):
        super().__init__(reason)
        self.result = result


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
        "swap_used_mb": swap_used_mb(),
        "other_containers": others,
    }


def swap_used_mb() -> float | None:
    """`used` de `sysctl vm.swapusage`. No invalida nada: tiene que poder verse."""
    raw = sh(["sysctl", "-n", "vm.swapusage"], check=False)
    found = re.search(r"used\s*=\s*([\d.]+)M", raw)
    return float(found.group(1)) if found else None


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
        raise PreconditionFailed("Precondiciones incumplidas, no se mide: " + "; ".join(problems))
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
        out["cpu"] = cgroup_cpu(cid)
        out["mongo"] = self.mongo_state()
        return out

    def mongo_state(self) -> dict:
        """Lo que se registra de MongoDB, que entra en la medida con 1:N.

        Solo se anota, no invalida nada. `cpu.stat` ANTES de `mongosh`, que es un
        proceso de Node dentro del cgroup de mongo. `opLatencies.reads`, en
        crudo: latencia acumulada (µs) y operaciones de lectura.
        """
        cid = self.compose("ps", "-q", "mongo", check=False).strip()
        if not cid:
            return {"running": False}
        found = json.loads(sh(["docker", "inspect", cid], check=False) or "[]")
        if not found:
            return {"container": cid[:12], "running": False}
        inspect = found[0]
        out = {
            "container": cid[:12],
            "started_at": inspect["State"]["StartedAt"],
            "restart_count": inspect["RestartCount"],
            "running": inspect["State"]["Running"],
            "oom_killed": inspect["State"]["OOMKilled"],
        }
        if not out["running"]:
            return out
        out["cpu"] = cgroup_cpu(cid)
        reads = "EJSON.stringify(db.serverStatus().opLatencies.reads, {relaxed: true})"
        raw = sh(["docker", "exec", cid, "mongosh", "--quiet", "--eval", reads], check=False)
        try:
            out["op_latencies_reads"] = json.loads(raw.strip().splitlines()[-1])
        except (ValueError, IndexError):
            out["op_latencies_reads"] = None
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


def cgroup_cpu(cid: str) -> dict:
    """`cpu.stat` del cgroup del contenedor, con la hora de la lectura.

    La vCPU media entre dos lecturas es Δusage_usec / Δsampled_at / 10⁶. Entre
    `service_before` y `service_after` abarca el calentamiento y la ventana.
    """
    t0 = time.time()
    raw = sh(["docker", "exec", cid, "cat", "/sys/fs/cgroup/cpu.stat"], check=False)
    t1 = time.time()
    stat = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            stat[parts[0]] = int(parts[1])
    return {"sampled_at": round((t0 + t1) / 2, 3), **stat}


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
            swaps = (host_before["swap_used_mb"], host_after["swap_used_mb"])
            swap_growth = round(swaps[1] - swaps[0], 2) if None not in swaps else None
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
                "swap_growth_mb": swap_growth,
                "service_before": before,
                "service_after": after,
                "k6_stdout": done.stdout.strip(),
            }
            meta_text = json.dumps(meta, indent=1, ensure_ascii=False)
            (self.dir / f"{stem}.meta.json").write_text(meta_text)
            self.last = meta
            line = f"{stem}: " + (
                "INVÁLIDA, " + reason
                if reason
                else ("pasa" if verdict["pass"] else "no pasa")
                + f" · éxito {verdict['success']:.3f} · descartadas {verdict['dropped']}"
                f" · en vuelo {verdict['inflight_max']}/{verdict['max_vus']}"
                f" · mediana {verdict['med_first']:.0f}→{verdict['med_last']:.0f} ms"
                f" (×{verdict['ratio']:.2f})"
            )
            if swap_growth:
                line += f" · swap {swap_growth:+.0f} MB"
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
    if args.op == "searchGallery":
        return f"{args.op}-{args.model}-{args.faces}"
    return f"{args.op}-{args.model}" if args.op == "verifyPair" else args.op


def cmd_most(args: argparse.Namespace, runner: Runner) -> dict:
    mit, mst = args.mit, None
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
    return {"mit": mit, "mst": mst}


def cmd_drift(args: argparse.Namespace, runner: Runner) -> dict:
    passed = runner.run("drift", args.rate, window=args.window, slices=args.slices)
    return {"pass": passed, "verdict": runner.last["verdict"]}


def execute(args: argparse.Namespace) -> dict:
    """Un paso completo: precondiciones, banco arriba, corridas, banco abajo."""
    preconditions(args.skip_preconditions)
    service = Service(args.target, args.workers, args.threads, args.concurrency)
    try:
        print(f"{now()} · {profile_name(args)} · {service.tag} · arrancando", flush=True)
        service.up()
        runner = Runner(args, service)
        seeded = None
        if args.op == "searchGallery":
            seeded = seed_gallery(service, args.faces, runner.dir / f"{runner.seq():03d}-gallery")
        result = (cmd_most if args.cmd == "most" else cmd_drift)(args, runner)
        if seeded is not None:
            end_path = runner.dir / f"{runner.seq():03d}-gallery-end.json"
            end = check_gallery(service, seeded, end_path)
            result["gallery"] = {**seeded, "end_ok": end["ok"]}
            if not end["ok"]:
                raise GalleryInvalid("comprobación final: " + "; ".join(end["problems"]), result)
        return result
    finally:
        if not args.keep:
            service.down()


# ------------------------------------------------------------ galería 1:N


def seed_tool(service: Service, *args: str) -> tuple[dict | None, str]:
    """`seed.py` en su contenedor: el JSON de su última línea y el error."""
    done = subprocess.run(
        [*COMPOSE, "--profile", "tools", "run", "--rm", "--no-deps", "seed", *args],
        capture_output=True,
        text=True,
        env=service.env,
    )
    lines = done.stdout.strip().splitlines()
    try:
        return json.loads(lines[-1]), done.stderr[-1000:]
    except (ValueError, IndexError):
        return None, f"salida {done.returncode}: {done.stdout[-500:]} {done.stderr[-1000:]}"


def seed_gallery(service: Service, faces: int, stem: Path | None) -> dict:
    """Siembra y comprobación inicial, que bloquea; deja la galería a k6."""
    started = time.monotonic()
    seeded, err = seed_tool(service, "seed", "--faces", str(faces))
    if seeded is None or "gallery" not in seeded:
        raise Abort(f"la siembra de {faces} rostros falló: {seeded or err}")
    seeded["wall_s"] = round(time.monotonic() - started, 2)
    if stem is not None:
        stem.with_name(stem.name + "-seed.json").write_text(json.dumps(seeded, indent=1))
    print(f"{now()} · galería de {faces} sembrada en {seeded['wall_s']} s · {seeded}", flush=True)
    path = stem.with_name(stem.name + "-start.json") if stem is not None else None
    start = check_gallery(service, seeded, path)
    if not start["ok"]:
        raise Abort("comprobación inicial: " + "; ".join(start["problems"]))
    service.env["GALLERY"] = seeded["gallery"]
    return seeded


def check_gallery(service: Service, seeded: dict, path: Path | None) -> dict:
    """N rostros, contadores, forma BSON y una búsqueda que coincide."""
    result, err = seed_tool(
        service,
        "check",
        "--faces",
        str(seeded["faces"]),
        "--gallery",
        seeded["gallery"],
        "--identity",
        seeded["identity"],
    )
    if result is None or "ok" not in result:
        result = {"ok": False, "problems": [f"la comprobación no terminó: {result or err}"]}
    result.update(at=now(), seed=seeded, mongo=service.mongo_state())
    if path is not None:
        path.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    verdict = "bien" if result["ok"] else "FALLA: " + "; ".join(result["problems"])
    print(f"{now()} · comprobación de la galería: {verdict}", flush=True)
    return result


# ------------------------------------------------------------ cadena nocturna

# La criba de la exploración: (workers, hilos de torch, concurrencia), con
# workers × hilos × concurrencia ≤ 8, los vCPU de fr.
CRIBA = [(1, 8, 1), (1, 4, 2), (1, 2, 4), (1, 1, 8), (2, 1, 1), (4, 1, 1), (8, 1, 1)]
CRIBA_BRACKET = (5.0, 25.0)
DRIFT_FRACTION = 0.9


class Chain:
    """criba → MST de las dos mejores → deriva → MoST de verifyPair xs.

    Un solo proceso, sin depender de nadie. Lo que va haciendo, y por qué se
    para si se para, queda en `results/chain-status.json`.
    """

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.path = Path(args.results).resolve() / "chain-status.json"
        self.status = {
            "state": "running",
            "started": now(),
            "stage": None,
            "reason": None,
            "criba": {},
            "mst": {},
            "chosen": None,
            "drift": None,
            "campaign": None,
        }

    def save(self, **changes: object) -> None:
        self.status.update(changes, updated=now())
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.status, indent=1, ensure_ascii=False))
        tmp.replace(self.path)

    def step(self, cmd: str, serie: str, cfg: tuple[int, int, int], **extra: object) -> dict:
        w, t, c = cfg
        args = argparse.Namespace(
            cmd=cmd,
            serie=serie,
            target="fr",
            op="verifyPair",
            model="xs",
            workers=w,
            threads=t,
            concurrency=c,
            results=self.args.results,
            skip_preconditions=False,
            keep=False,
            only=None,
            mit=None,
            bracket=None,
            no_confirm=False,
        )
        for key, val in extra.items():
            setattr(args, key, val)
        return execute(args)

    def run(self) -> None:
        tag = "w{}t{}c{}".format
        self.save(stage="criba")
        for cfg in CRIBA:
            try:
                got = self.step(
                    "most",
                    "exploration",
                    cfg,
                    only="mit",
                    bracket=list(CRIBA_BRACKET),
                    no_confirm=True,
                )
                self.status["criba"][tag(*cfg)] = {"mit": got["mit"]}
            except PreconditionFailed:
                raise
            except Abort as exc:
                self.status["criba"][tag(*cfg)] = {"error": str(exc)}
            self.save()
        ranked = sorted(
            (cfg for cfg in CRIBA if self.status["criba"][tag(*cfg)].get("mit")),
            key=lambda cfg: -self.status["criba"][tag(*cfg)]["mit"],
        )
        if not ranked:
            return self.save(state="stopped", reason="ninguna configuración superó la criba")

        self.save(stage="mst")
        for cfg in ranked[:2]:
            mit = self.status["criba"][tag(*cfg)]["mit"]
            try:
                got = self.step("most", "exploration", cfg, only="mst", mit=mit, no_confirm=True)
                self.status["mst"][tag(*cfg)] = {"mit": mit, "mst": got["mst"]}
            except PreconditionFailed:
                raise
            except Abort as exc:
                self.status["mst"][tag(*cfg)] = {"mit": mit, "error": str(exc)}
            self.save()
        done = [cfg for cfg in ranked[:2] if self.status["mst"][tag(*cfg)].get("mst")]
        if not done:
            return self.save(state="stopped", reason="ningún MST de las dos mejores")
        # Mayor MST; a igualdad, mayor MIT.
        best = max(
            done,
            key=lambda cfg: (
                self.status["mst"][tag(*cfg)]["mst"],
                self.status["mst"][tag(*cfg)]["mit"],
            ),
        )
        chosen = {"config": tag(*best), **self.status["mst"][tag(*best)]}
        self.save(stage="drift", chosen=chosen)

        rate = rnd(DRIFT_FRACTION * chosen["mit"])
        got = self.step("drift", "drift", best, rate=rate, window=900, slices=15)
        self.save(drift={"rate": rate, **got})
        if not got["pass"]:
            v = got["verdict"]
            return self.save(
                state="stopped",
                reason=(
                    "la deriva no cumple las tres restricciones con tercios de 5 min: "
                    f"{v['checks']}; mediana {v['med_first']}→{v['med_last']} ms (×{v['ratio']}). "
                    "No se mide xs."
                ),
            )

        self.save(stage="campaign")
        got = self.step("most", "campaign", best)
        self.save(state="finished", stage=None, campaign=got)


def cmd_chain(args: argparse.Namespace) -> None:
    chain = Chain(args)
    try:
        preconditions(False)
        chain.run()
    except Abort as exc:
        chain.save(state="stopped", reason=str(exc))
        raise
    except BaseException as exc:
        chain.save(state="failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    print(f"{now()} · cadena: {chain.status['state']} · {chain.status['reason'] or ''}", flush=True)


# ------------------------------------------------------------ perfiles 1:N

SEARCH_SIZES = (5000, 20000, 50000)


def cmd_search(args: argparse.Namespace) -> None:
    """Los tres perfiles 1:N en secuencia, fijados como `Chain.step`.

    Un perfil que falla (siembra, comprobación o búsqueda) se anota con su motivo
    y la secuencia sigue con el siguiente; una precondición incumplida la para.
    """
    path = Path(args.results).resolve() / "search-status.json"
    status = {"state": "running", "started": now(), "stage": None, "reason": None, "profiles": {}}

    def save(**changes: object) -> None:
        status.update(changes, updated=now())
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=1, ensure_ascii=False))
        tmp.replace(path)

    try:
        save()
        preconditions(False)
        for faces in SEARCH_SIZES:
            step = argparse.Namespace(
                cmd="most",
                serie="campaign",
                target="fr",
                op="searchGallery",
                model="xs",
                faces=faces,
                workers=1,
                threads=1,
                concurrency=8,
                results=args.results,
                skip_preconditions=False,
                keep=False,
                only=None,
                mit=None,
                bracket=None,
                no_confirm=False,
            )
            name = profile_name(step)
            save(stage=name)
            try:
                status["profiles"][name] = {"valid": True, **execute(step)}
            except PreconditionFailed:
                raise
            except GalleryInvalid as exc:
                status["profiles"][name] = {"valid": False, "reason": str(exc), **exc.result}
            except Abort as exc:
                status["profiles"][name] = {"valid": False, "reason": str(exc)}
            save()
        save(state="finished", stage=None)
    except Abort as exc:
        save(state="stopped", reason=str(exc))
        raise
    except BaseException as exc:
        save(state="failed", reason=f"{type(exc).__name__}: {exc}")
        raise
    print(f"{now()} · 1:N: {status['state']}", flush=True)


def cmd_seed(args: argparse.Namespace) -> None:
    """Solo la siembra y su comprobación, con sus tiempos. No mide nada."""
    service = Service("fr", 1, 1, 8)
    try:
        service.up()
        started = time.monotonic()
        seeded = seed_gallery(service, args.faces, None)
        print(json.dumps({**seeded, "seed_and_check_s": round(time.monotonic() - started, 2)}))
    finally:
        if not args.keep:
            service.down()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    chain = sub.add_parser("chain", help="la cadena nocturna, en un solo proceso")
    chain.add_argument("--results", default=str(LOAD / "results"))
    search = sub.add_parser("search", help="los tres perfiles 1:N, en un solo proceso")
    search.add_argument("--results", default=str(LOAD / "results"))
    seed = sub.add_parser("seed", help="solo la siembra de una galería, con su tiempo")
    seed.add_argument("--faces", type=int, required=True)
    seed.add_argument("--keep", action="store_true", help="no parar el compose al acabar")
    for name in ("most", "drift"):
        p = sub.add_parser(name)
        p.add_argument("--serie", required=True)
        p.add_argument("--target", choices=["fr", "calib"], default="fr")
        p.add_argument(
            "--op", choices=["verifyPair", "detectFaces", "searchGallery"], default="verifyPair"
        )
        p.add_argument("--faces", type=int, help="rostros de la galería (searchGallery)")
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
    if args.cmd in ("most", "drift"):
        if (args.op == "searchGallery") != (args.faces is not None):
            parser.error("--faces va con --op searchGallery, y solo con ella")
        if args.op == "searchGallery" and args.target != "fr":
            parser.error("searchGallery solo con --target fr")
    if args.cmd == "chain":
        cmd_chain(args)
    elif args.cmd == "search":
        cmd_search(args)
    elif args.cmd == "seed":
        cmd_seed(args)
    else:
        execute(args)


if __name__ == "__main__":
    main()
