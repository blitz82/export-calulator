from types import SimpleNamespace

import launch


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
