import argparse
import ctypes
import hashlib
import logging
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from epms.api import create_app


WEBVIEW2_DOWNLOAD = "https://developer.microsoft.com/microsoft-edge/webview2/#download"


def has_webview2():
    import winreg

    key = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root, access in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_READ | winreg.KEY_WOW64_32KEY),
                         (winreg.HKEY_CURRENT_USER, winreg.KEY_READ)):
        try:
            with winreg.OpenKey(root, key, 0, access) as handle:
                version, kind = winreg.QueryValueEx(handle, "pv")
            parts = version.split(".") if kind == winreg.REG_SZ else []
            if len(parts) == 4 and all(part.isascii() and part.isdecimal() for part in parts) and any(map(int, parts)):
                return True
        except OSError:
            continue
    return False


def show_webview2_download():
    message = (
        "Microsoft Edge WebView2 Runtime을 설치한 뒤 프로그램을 다시 실행하세요.\n\n"
        "Microsoft 다운로드 페이지에서 Evergreen Bootstrapper 또는 x64 독립 실행형 설치 파일을 선택하세요.\n"
        f"{WEBVIEW2_DOWNLOAD}\n\n"
        "지금 다운로드 페이지를 열까요?"
    )
    if ctypes.windll.user32.MessageBoxW(None, message, "WebView2 설치 필요", 0x124) == 6:
        os.startfile(WEBVIEW2_DOWNLOAD)


def accept_fixed_runtime_terms(package_root):
    terms = package_root / "licenses" / "runtime" / "WebView2-Fixed-Version-LICENSE.html"
    if not terms.is_file():
        raise RuntimeError("무설치판의 WebView2 사용 약관 파일을 찾을 수 없습니다.")
    digest = hashlib.sha256(terms.read_bytes()).hexdigest()
    consent = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EPMS" / f"webview2-terms-{digest}.accepted"
    if consent.is_file():
        return True
    message = (
        "무설치판에 포함된 Microsoft Edge WebView2 Runtime (Fixed Version) 약관을 확인하세요.\n\n"
        "Microsoft Defender SmartScreen은 Microsoft 개인정보처리방침에 따라 "
        "최종 사용자의 정보를 Microsoft에 보내고 처리할 수 있습니다.\n\n"
        "예: 약관에 동의하고 실행 · 아니요: 약관 전문 열기 · 취소: 종료"
    )
    while True:
        choice = ctypes.windll.user32.MessageBoxW(None, message, "Microsoft WebView2 사용 약관", 0x223)
        if choice == 7:
            os.startfile(terms)
            continue
        if choice != 6:
            return False
        consent.parent.mkdir(parents=True, exist_ok=True)
        consent.touch()
        return True


def desktop(app, server, listener, port):
    import webview

    packaged = getattr(sys, "frozen", False)
    runtime = (Path(sys.executable).parent / "WebView2" if packaged
               else Path(__file__).resolve().parent / "build" / "WebView2")
    fixed_runtime = (runtime / "msedgewebview2.exe").is_file()
    if packaged and fixed_runtime and not accept_fixed_runtime_terms(runtime.parent):
        return
    if fixed_runtime and sys.getwindowsversion().build < 22000:
        # Fixed WebView2 on Windows 10 runs its renderer in an AppContainer.
        result = subprocess.run(
            [str(Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"), str(runtime),
             "/grant", "*S-1-15-2-1:(OI)(CI)(RX)", "*S-1-15-2-2:(OI)(CI)(RX)"],
            creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True,
        )
        if result.returncode:
            raise RuntimeError("WebView2 폴더의 읽기 권한을 설정할 수 없습니다. 쓰기 가능한 로컬 폴더에 압축을 풀어 주세요.")
    if fixed_runtime:
        os.environ["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] = str(runtime)
    else:
        os.environ.pop("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", None)
    os.environ.pop("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", None)
    webview.settings.update(WEBVIEW2_RUNTIME_PATH=str(runtime) if fixed_runtime else None, ALLOW_DOWNLOADS=True,
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
    os.environ.setdefault("EPMS_SORTING_ENABLED", "1")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--db")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--browser", action="store_true", help="Open the local app in the default PC browser")
    args = parser.parse_args()
    if not args.no_browser and not args.browser and getattr(sys, "frozen", False):
        fixed_runtime = Path(sys.executable).parent / "WebView2" / "msedgewebview2.exe"
        if not fixed_runtime.is_file() and not has_webview2():
            show_webview2_download()
            return
    app = create_app(args.db)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", args.port))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False,
                                               log_config=None, timeout_graceful_shutdown=10))
        try:
            if args.no_browser or args.browser:
                def stop_server():
                    server.should_exit = True

                app.state.shutdown = stop_server
                if args.browser:
                    def open_browser():
                        import time
                        while not server.started and not server.should_exit:
                            time.sleep(0.1)
                        if server.started:
                            webbrowser.open(f"http://127.0.0.1:{port}/")
                    threading.Thread(target=open_browser, daemon=True).start()
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
