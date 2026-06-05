from __future__ import annotations

import argparse
import base64
import ipaddress
from io import BytesIO
from pathlib import Path
import re
import subprocess  # nosec B404 - used only to relaunch this module via pythonw.
import sys
import threading
import traceback
import webbrowser

import requests

from chemunited_workflow.api import create_api
from chemunited_workflow.api.dependencies import get_project_holder
from chemunited_workflow.project_loader import load_project


PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
DEFAULT_ICON_PATH = PACKAGE_ROOT / "chemunited.svg"
ERROR_LOG_PATH = REPO_ROOT / "tray_launcher.log"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run chemunited-workflow FastAPI in the Windows system tray."
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Project directory containing protocols/ and connectivity/.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="FastAPI bind host.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3116,
        help="FastAPI bind port.",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="URL opened by the tray menu. Defaults to http://{host}:{port}/docs.",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        default=False,
        help="On Windows, relaunch with pythonw.exe so no terminal window stays open.",
    )
    return parser.parse_args(argv)


def build_base_url(host: str, port: int) -> str:
    display_host = host
    try:
        parsed_host = ipaddress.ip_address(host)
    except ValueError:
        parsed_host = None

    if parsed_host is not None:
        if parsed_host.is_unspecified:
            display_host = "127.0.0.1"
        elif parsed_host.version == 6:
            display_host = f"[{host}]"
    return f"http://{display_host}:{port}"


def build_app_url(host: str, port: int, url: str | None = None) -> str:
    return url or f"{build_base_url(host, port)}/docs"


def _square_icon(image, size: int = 64):
    from PIL import Image, ImageOps

    image = ImageOps.contain(image.convert("RGBA"), (size, size))
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = ((size - image.width) // 2, (size - image.height) // 2)
    canvas.paste(image, offset, image)
    return canvas


def load_embedded_png_icon(svg_path: Path, size: int = 64):
    from PIL import Image

    text = svg_path.read_text(encoding="utf-8")
    match = re.search(r"data:image/png;base64,([^\"']+)", text, re.DOTALL)
    if not match:
        raise ValueError(f"No embedded PNG found in {svg_path}")

    png_bytes = base64.b64decode("".join(match.group(1).split()))
    with Image.open(BytesIO(png_bytes)) as image:
        return _square_icon(image, size=size)


def create_fallback_icon(size: int = 64):
    return create_tray_badge_icon(size=size)


def create_tray_badge_icon(size: int = 64):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    center = size / 2
    outer_margin = max(1, size * 0.04)
    white_margin = size * 0.18
    blue_margin = size * 0.24

    draw.ellipse(
        (
            outer_margin,
            outer_margin,
            size - outer_margin - 1,
            size - outer_margin - 1,
        ),
        fill=(40, 47, 56, 255),
    )
    draw.ellipse(
        (
            white_margin,
            white_margin,
            size - white_margin - 1,
            size - white_margin - 1,
        ),
        fill=(255, 255, 255, 255),
    )
    draw.ellipse(
        (
            blue_margin,
            blue_margin,
            size - blue_margin - 1,
            size - blue_margin - 1,
        ),
        fill=(30, 144, 255, 255),
    )

    point_radius = max(1.5, size * 0.075)
    point_offset = size * 0.31
    for x, y in (
        (center, center - point_offset),
        (center + point_offset, center),
        (center, center + point_offset),
        (center - point_offset, center),
    ):
        draw.ellipse(
            (
                x - point_radius,
                y - point_radius,
                x + point_radius,
                y + point_radius,
            ),
            fill=(255, 255, 255, 255),
        )
    return image


def load_tray_icon_image(svg_path: Path = DEFAULT_ICON_PATH, size: int = 64):
    try:
        return load_embedded_png_icon(svg_path, size=size)
    except Exception:
        return create_fallback_icon(size=size)


def create_fastapi_app(project_dir: Path | None):
    app = create_api()
    if project_dir is not None:
        modules = load_project(project_dir.resolve())
        app.dependency_overrides[get_project_holder]().load(modules)
    return app


def create_uvicorn_config(app, host: str, port: int):
    import uvicorn

    return uvicorn.Config(
        app,
        host=host,
        port=port,
        access_log=False,
        log_config=None,
        log_level="warning",
    )


def start_uvicorn_thread(app, host: str, port: int):
    import uvicorn

    config = create_uvicorn_config(app, host=host, port=port)
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        name="chemunited-fastapi",
        daemon=True,
    )
    thread.start()
    return server, thread


def stop_server(server, thread: threading.Thread, timeout: float = 5.0) -> None:
    server.should_exit = True
    if thread.is_alive():
        thread.join(timeout=timeout)


def run_tray(server, server_thread: threading.Thread, app_url: str) -> None:
    import pystray

    def open_app(icon, item):
        webbrowser.open(app_url)

    def show_status(icon, item):
        icon.notify("FastAPI app is running", "chemunited-workflow")

    def quit_app(icon, item):
        stop_server(server, server_thread)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open App", open_app),
        pystray.MenuItem("Status", show_status),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon(
        "chemunited-workflow",
        load_tray_icon_image(),
        "chemunited-workflow",
        menu,
    )
    icon.run()


def is_api_reachable(base_url: str, timeout: float = 0.5) -> bool:
    try:
        response = requests.get(f"{base_url}/project/", timeout=timeout)
    except requests.RequestException:
        return False
    return response.status_code == 200


def load_project_into_running_api(
    base_url: str,
    project_dir: Path,
    timeout: float = 10.0,
) -> dict:
    response = requests.put(
        f"{base_url}/project/",
        json={"project_dir": str(project_dir.resolve())},
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            "Failed to load project into the running chemunited API "
            f"at {base_url}: HTTP {response.status_code} {response.text}"
        )
    return response.json()


def _pythonw_executable() -> Path | None:
    executable = Path(sys.executable)
    if executable.stem.lower() == "pythonw":
        return None

    candidate = executable.with_name("pythonw.exe")
    if candidate.exists():
        return candidate
    return None


def relaunch_silently(argv: list[str]) -> bool:
    pythonw = _pythonw_executable()
    if pythonw is None:
        return False

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )

    subprocess.Popen(  # nosec B603 - shell=False and argv relaunches this module.
        [str(pythonw), "-m", "chemunited_workflow.tray_launcher", *argv],
        close_fds=True,
        creationflags=creationflags,
    )
    return True


def run(argv: list[str] | None = None) -> None:
    try:
        ERROR_LOG_PATH.unlink(missing_ok=True)
    except OSError as unlink_error:
        if sys.stderr is not None:
            print(
                f"Failed to clear old tray launcher log: {unlink_error}",
                file=sys.stderr,
            )

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(raw_argv)
    if args.silent and relaunch_silently(raw_argv):
        return

    base_url = build_base_url(args.host, args.port)
    if is_api_reachable(base_url):
        if args.project_dir is not None:
            load_project_into_running_api(base_url, args.project_dir.resolve())
        return

    project_dir = args.project_dir.resolve() if args.project_dir is not None else None
    app_url = build_app_url(args.host, args.port, args.url)
    app = create_fastapi_app(project_dir)
    server, server_thread = start_uvicorn_thread(app, args.host, args.port)
    run_tray(server, server_thread, app_url)


def main(argv: list[str] | None = None) -> None:
    try:
        run(argv)
    except Exception as exc:
        _report_startup_error(exc)
        if Path(sys.executable).stem.lower() != "pythonw":
            raise


def _report_startup_error(exc: BaseException) -> None:
    ERROR_LOG_PATH.write_text(traceback.format_exc(), encoding="utf-8")
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"Tray launcher failed.\n\nDetails were written to:\n{ERROR_LOG_PATH}",
            "chemunited-workflow",
            0x10,
        )
    except Exception as dialog_error:
        if sys.stderr is not None:
            print(
                f"Failed to show startup error dialog: {dialog_error}", file=sys.stderr
            )


if __name__ == "__main__":
    main()
