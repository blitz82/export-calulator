import argparse
import ctypes
import logging
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import uvicorn

from epms.api import create_app


def desktop(app, server, listener, port):
    import webview

    packaged = getattr(sys, "frozen", False)
    runtime = (Path(sys.executable).parent / "WebView2" if packaged
               else Path(__file__).resolve().parent / "build" / "WebView2")
    if not (runtime / "msedgewebview2.exe").is_file():
        raise RuntimeError("배포 폴더에서 WebView2 런타임을 찾을 수 없습니다. ZIP 전체를 로컬 디스크에 풀어 주세요.")
    if sys.getwindowsversion().build < 22000:
        # Fixed WebView2 on Windows 10 runs its renderer in an AppContainer.
        result = subprocess.run(
            [str(Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"), str(runtime),
             "/grant", "*S-1-15-2-1:(OI)(CI)(RX)", "*S-1-15-2-2:(OI)(CI)(RX)"],
            creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True,
        )
        if result.returncode:
            raise RuntimeError("WebView2 폴더의 읽기 권한을 설정할 수 없습니다. 쓰기 가능한 로컬 폴더에 압축을 풀어 주세요.")
    os.environ["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] = str(runtime)
    os.environ.pop("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", None)
    webview.settings.update(WEBVIEW2_RUNTIME_PATH=str(runtime), ALLOW_DOWNLOADS=True,
                            ALLOW_FILE_URLS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        started = threading.Event()
        for _ in range(200):
            if server.started or not thread.is_alive():
                break
            started.wait(0.05)
        if not server.started:
            raise RuntimeError("로컬 업무 서버를 시작할 수 없습니다.")
        window = webview.create_window("공동정산 수출실적 관리", f"http://127.0.0.1:{port}/",
                                       width=1280, height=850, min_size=(820, 570))
        app.state.shutdown = window.destroy
        webview.start(gui="edgechromium")
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--db")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    app = create_app(args.db)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", args.port))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False,
                                               log_config=None, timeout_graceful_shutdown=10))
        try:
            if args.no_browser:
                def stop_server():
                    server.should_exit = True

                app.state.shutdown = stop_server
                server.run(sockets=[listener])
            else:
                desktop(app, server, listener, port)
        finally:
            app.state.db.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if not getattr(sys, "frozen", False) and sys.stdout is not None:
            raise
        data_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EPMS"
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(filename=data_dir / "startup.log", encoding="utf-8", level=logging.ERROR)
            logging.exception("공동정산 수출실적 관리 시작 실패")
        finally:
            message = str(exc) if isinstance(exc, RuntimeError) else "프로그램을 시작할 수 없습니다. 시작 로그를 확인하세요."
            ctypes.windll.user32.MessageBoxW(None, message, "공동정산 수출실적 관리 실행 오류", 0x10)
        sys.exit(1)
