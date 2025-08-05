import json
import threading
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.connection import Connection
from websockets.sync.server import serve


class WebSocketBroadcaster:
    def __init__(self, port: int, *, path: str, banner: str):
        self._port = port
        self._path = path
        self._banner = banner
        self._clients: set[Connection] = set()
        self._clients_lock = threading.Lock()

        thread = threading.Thread(target=self._run_server, daemon=True)
        thread.start()

    def _run_server(self) -> None:
        serve(self._handler, host="localhost", port=self._port).serve_forever()

    def _handler(self, ws: Connection) -> None:
        if ws.request is None or ws.request.path != self._path:
            ws.close(code=1008, reason="Invalid path")
            return

        with self._clients_lock:
            self._clients.add(ws)

        try:
            ws.send(self._banner)
            while True:
                try:
                    ws.recv(timeout=60)  # Keep alive by blocking
                except TimeoutError:
                    pass
        except ConnectionClosed:
            pass
        finally:
            with self._clients_lock:
                self._clients.discard(ws)

    def send_state(self, state: dict[Any, Any]) -> None:
        message = json.dumps(state)
        with self._clients_lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                ws.send(message)
            except ConnectionClosed:
                self._clients.discard(ws)
