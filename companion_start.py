"""Starts everything the AI companion needs with one command:

1. the companion server (companion_main.py)
2. a Cloudflare quick tunnel, so the AI app can reach the server from the internet
3. optionally the fake game (--fake-game), to try things out without Skyrim

Run from the repo root:  python companion_start.py --fake-game
Stop everything with Ctrl+C.

Needs cloudflared (Windows: winget install --id Cloudflare.cloudflared).
"""
import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

TOKEN_FILE: str = "companion_token.txt"
DEFAULT_PORT: int = 4998
SERVER_START_TIMEOUT_SECONDS: float = 20.0
TUNNEL_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
MCP_ROUTE_PREFIX: str = "/mcp/"

processes: list[subprocess.Popen] = []
tunnel_url_found = threading.Event()
tunnel_url: list[str] = []


def start_process(name: str, command: list[str], watch_for_tunnel_url: bool = False) -> subprocess.Popen:
    """Starts a child process and prints its output line by line with the name in front"""
    environment = dict(os.environ)
    environment["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    processes.append(process)

    def forward_output():
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            if watch_for_tunnel_url and not tunnel_url_found.is_set():
                match = TUNNEL_URL_PATTERN.search(line)
                if match:
                    tunnel_url.append(match.group(0))
                    tunnel_url_found.set()
            print(f"[{name}] {line}", flush=True)

    threading.Thread(target=forward_output, daemon=True).start()
    return process


def wait_for_port(port: int, timeout_seconds: float) -> bool:
    end_time = time.monotonic() + timeout_seconds
    while time.monotonic() < end_time:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def stop_all():
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def print_box(lines: list[str]):
    width = max(len(line) for line in lines)
    print("", flush=True)
    print("=" * (width + 4))
    for line in lines:
        print(f"  {line}")
    print("=" * (width + 4))
    print("", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Starts the companion server, the tunnel and optionally the fake game")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--fake-game", action="store_true", help="Also start the fake game (for testing without Skyrim)")
    parser.add_argument("--no-tunnel", action="store_true", help="Do not start the Cloudflare tunnel")
    parser.add_argument("--cloudflared", default="cloudflared", help="Name or path of the cloudflared program")
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    cloudflared_path = None
    if not args.no_tunnel:
        cloudflared_path = shutil.which(args.cloudflared)
        if cloudflared_path is None:
            print("cloudflared was not found.")
            print("Install it with:  winget install --id Cloudflare.cloudflared")
            print("Then open a NEW terminal and start this script again. Or start without tunnel: --no-tunnel")
            return 1

    try:
        server = start_process("server", [sys.executable, "companion_main.py", "--port", str(args.port)])
        if not wait_for_port(args.port, SERVER_START_TIMEOUT_SECONDS):
            print(f"The companion server did not start on port {args.port}. See the [server] lines above.")
            return 1

        with open(TOKEN_FILE, "r", encoding="utf-8") as file:
            token = file.read().strip()

        if args.fake_game:
            start_process("game", [sys.executable, "-m", "src.companion.fake_game", "--port", str(args.port)])

        if cloudflared_path is not None:
            start_process("tunnel", [cloudflared_path, "tunnel", "--url", f"http://localhost:{args.port}"], watch_for_tunnel_url=True)
            if tunnel_url_found.wait(timeout=60):
                print_box([
                    "Everything is running. Address for the AI app (custom connector):",
                    "",
                    f"{tunnel_url[0]}{MCP_ROUTE_PREFIX}{token}",
                    "",
                    "The address changes every time the tunnel is started. Stop with Ctrl+C.",
                ])
            else:
                print("The tunnel did not report an address within 60 seconds. See the [tunnel] lines above.")
        else:
            print_box([
                "Server is running without tunnel. Local MCP address:",
                "",
                f"http://localhost:{args.port}{MCP_ROUTE_PREFIX}{token}",
            ])

        while True:
            time.sleep(0.5)
            if server.poll() is not None:
                print("The companion server has stopped.")
                return 1
            for process in processes:
                if process is not server and process.poll() is not None:
                    print("One of the other programs has stopped. See the lines above.")
                    return 1
    except KeyboardInterrupt:
        print("Stopping...")
        return 0
    finally:
        stop_all()


if __name__ == "__main__":
    sys.exit(main())
