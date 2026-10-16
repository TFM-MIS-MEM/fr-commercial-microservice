"""Los modelos se cargan en el arranque, después de los índices, o no hay arranque.

uvicorn no acepta conexiones hasta que termina el `lifespan`; con `TestClient`,
el `lifespan` corre al entrar en el gestor de contexto. Si lanza, no hay
servicio: tampoco `/health`.
"""

import asyncio
import shutil
from dataclasses import replace

import pytest
import torch
from fastapi.testclient import TestClient

from src.controllers.inference import run
from src.core.settings import CHECKPOINTS_DIR
from src.main import create_app
from tests.contract.harness import BASE_URL
from tests.support.settings import settings


def test_without_a_weights_file_the_service_does_not_start(test_db_uri, db, tmp_path):
    for weights in CHECKPOINTS_DIR.iterdir():
        if weights.name != "edgeface_base.pt":
            shutil.copy(weights, tmp_path / weights.name)
    app = create_app(settings(test_db_uri, checkpoints_dir=tmp_path))

    with pytest.raises(FileNotFoundError, match="edgeface_base.pt"):
        with TestClient(app, base_url=BASE_URL):
            pass


def test_with_every_weights_file_health_answers_and_the_models_are_there(client):
    res = client.get("/health")

    assert res.status_code == 200
    assert set(client.app.state.inference.engine.models) == {"xs", "s", "base"}


def test_the_campaign_knobs_reach_the_semaphore_and_the_engine(test_db_uri, db):
    app = create_app(settings(test_db_uri, torch_num_threads=2, inference_concurrency=3))

    with TestClient(app, base_url=BASE_URL):
        assert app.state.inference.semaphore._value == 3
        assert app.state.inference.torch_threads == 2


def test_the_torch_threads_are_set_in_the_worker_that_infers(client):
    """El ajuste es por hilo (src/controllers/inference.py): se comprueba en el
    hilo que ejecuta el trabajo, no en el que arrancó el servicio."""
    inference = replace(client.app.state.inference, semaphore=asyncio.Semaphore(1))
    before = asyncio.run(run(inference, torch.get_num_threads))
    try:
        assert asyncio.run(run(replace(inference, torch_threads=2), torch.get_num_threads)) == 2
        assert asyncio.run(run(replace(inference, torch_threads=3), torch.get_num_threads)) == 3
    finally:
        asyncio.run(run(replace(inference, torch_threads=before), torch.get_num_threads))
