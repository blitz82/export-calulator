from types import SimpleNamespace
import sys

import pytest

import launch


@pytest.mark.parametrize('headless', [False, True])
def test_native_launch_uses_one_database_and_enables_features(tmp_path, monkeypatch, headless):
    monkeypatch.delenv('EPMS_SORTING_ENABLED', raising=False)
    database = str(tmp_path / 'same-local.db')
    monkeypatch.setattr(sys, 'argv', ['launch.py', '--db', database] + (['--no-browser'] if headless else []))
    observed = {}
    app = SimpleNamespace(state=SimpleNamespace(db=SimpleNamespace(dispose=lambda: observed.update(disposed=True))))
    def create_app(path):
        observed.update(path=path, enabled=launch.os.environ.get('EPMS_SORTING_ENABLED'))
        return app
    class Server:
        def __init__(self, config):
            pass
        def run(self, sockets):
            observed['server'] = sockets[0].getsockname()
    def desktop(app, server, listener, port):
        observed['native'] = ('127.0.0.1', port)
    monkeypatch.setattr(launch, 'create_app', create_app)
    monkeypatch.setattr(launch.uvicorn, 'Server', Server)
    monkeypatch.setattr(launch, 'desktop', desktop)
    launch.main()
    assert observed['path'] == database
    assert observed['enabled'] == '1'
    assert observed['disposed']
    assert observed['server' if headless else 'native'][0] == '127.0.0.1'
    assert ('native' in observed) != headless


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
