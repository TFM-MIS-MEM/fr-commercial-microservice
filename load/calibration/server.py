"""Servicio sintético de capacidad conocida, para calibrar el instrumento.

Atiende UNA petición a la vez y tarda 100 ms en cada una: su capacidad teórica
es de 10 por segundo, algo menos por el coste de leer el cuerpo y responder.
Si MoST no la encuentra, el instrumento no mide lo que creemos.

Lee el cuerpo entero antes de entrar en la sección crítica, igual que FastAPI
lee el multipart antes de la inferencia, y k6 le envía exactamente el mismo
cuerpo que a `fr`: el lado del generador se calibra tal como se usa. Las
peticiones que esperan el candado hacen cola en hilos sin tope, que es lo que
hace el semáforo de `INFERENCE_CONCURRENCY` en `fr`.

El tiempo de servicio es un `sleep`, no CPU: se calibra el instrumento (k6, las
restricciones y la búsqueda), no la máquina.
"""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE_TIME_S = 0.100
BODY = b'{"ok":true}'

_one_at_a_time = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def do_GET(self) -> None:  # noqa: N802
        self._reply(200 if self.path == "/health" else 404)

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        with _one_at_a_time:
            time.sleep(SERVICE_TIME_S)
        self._reply(200)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


class Server(ThreadingHTTPServer):
    # La cola de `listen` es de 5 por omisión: con decenas de VUs abriendo
    # conexión a la vez, las rechazaría el núcleo y no la sección crítica.
    request_queue_size = 128
    daemon_threads = True


if __name__ == "__main__":
    server = Server(("0.0.0.0", 8000), Handler)  # nosec B104
    server.serve_forever()
