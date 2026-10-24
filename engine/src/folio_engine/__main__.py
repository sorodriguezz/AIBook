"""Entry point: `folio-engine --port 0` prints the chosen port on stdout."""

import argparse
import logging
import os
import socket
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="folio-engine")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["FOLIO_DATA_DIR"] = args.data_dir

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)

    import uvicorn

    from .server import app

    sock = socket.socket()
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]
    # The Tauri side reads this line to learn where to connect.
    print(f"FOLIO_ENGINE_PORT={port}", flush=True)
    config = uvicorn.Config(app, log_level="warning", ws_ping_interval=20)
    server = uvicorn.Server(config)
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
