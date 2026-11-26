from types import SimpleNamespace
import sys
import threading

import pytest

import launch


@pytest.mark.parametrize('mode', ['--browser', '--no-browser'])
def test_local_launch_enables_current_features_and_uses_selected_database(tmp_path, monkeypatch, mode):
    monkeypatch.delenv('EPMS_SORTING_ENABLED', raising=False)
    database = str(tmp_path / 'same-local.db')
    monkeypatch.setattr(sys, 'argv', ['launch.py', mode, '--db', database])
    observed = {}
    opened = threading.Event()
    app = SimpleNamespace(state=SimpleNamespace(db=SimpleNamespace(dispose=lambda: observed.update(disposed=True))))

    def create_app(path):
        observed.update(path=path, enabled=launch.os.environ.get('EPMS_SORTING_ENABLED'))
        return app

    def open_browser(url):
        observed['url'] = url
        opened.set()

    class Server:
        started = False
        should_exit = False

        def __init__(self, config):
            pass

        def run(self, sockets):
            observed['address'] = sockets[0].getsockname()
            self.started = True
            if mode == '--browser':
                assert opened.wait(3)

    monkeypatch.setattr(launch, 'create_app', create_app)
    monkeypatch.setattr(launch.uvicorn, 'Server', Server)
    monkeypatch.setattr(launch.webbrowser, 'open', open_browser)
    launch.main()
    assert observed['path'] == database
    assert observed['enabled'] == '1'
    assert observed['address'][0] == '127.0.0.1'
    assert observed['disposed']
    if mode == '--browser':
        assert observed['url'] == f"http://127.0.0.1:{observed['address'][1]}/"
    else:
        assert not opened.is_set()


def test_fixed_runtime_license_opens_full_terms_and_records_acceptance(tmp_path, monkeypatch):
    terms = tmp_path / "licenses/runtime/WebView2-Fixed-Version-LICENSE.html"
    terms.parent.mkdir(parents=True)
    terms.write_text("Test terms", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    choices = iter((7, 6, 2))
    dialogs = []
    opened = []

    def answer(*args):
        dialogs.append(args)
        return next(choices)

    monkeypatch.setattr(launch.ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(MessageBoxW=answer)), raising=False)
    monkeypatch.setattr(launch.os, "startfile", opened.append, raising=False)
    assert launch.accept_fixed_runtime_terms(tmp_path)
    assert opened == [terms]
    assert launch.accept_fixed_runtime_terms(tmp_path)
    assert len(dialogs) == 2
    terms.write_text("Updated test terms", encoding="utf-8")
    assert not launch.accept_fixed_runtime_terms(tmp_path)
    assert len(dialogs) == 3
