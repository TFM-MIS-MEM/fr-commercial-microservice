"""Sonda de salud.

Fuera de `/v1` y sin identidad: la usa el orquestador, no un cliente. Si
exigiera las cabeceras de la pasarela, el contenedor se declararía enfermo y se
reiniciaría en bucle.

Responde `200` también con la base de datos caída, igual que `pricing`: es una
sonda de vida, y reiniciar el servicio no arregla la base de datos. El estado de
la base va en el cuerpo.
"""

from fastapi import APIRouter, Request

from src.core.database import ping

router = APIRouter(include_in_schema=False)


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    connected = await ping(request.app.state.db)
    return {"status": "ok", "db": "connected" if connected else "disconnected"}
