"""Tests for main() teardown cleanup hooks."""

from argparse import Namespace

import pytest

import main as main_mod


def test_main_releases_touched_ollama_models_on_value_error(monkeypatch):
    calls = {"release": 0, "reset": 0}

    monkeypatch.setattr(main_mod, "_parse_args", lambda: Namespace())
    monkeypatch.setattr(main_mod, "run_multi_service", lambda args: (_ for _ in ()).throw(ValueError("bad input")))  # noqa: ARG005,E501
    monkeypatch.setattr(
        main_mod,
        "release_touched_ollama_models",
        lambda: calls.__setitem__("release", calls["release"] + 1),
    )
    monkeypatch.setattr(
        main_mod,
        "reset_active_threshold_profile",
        lambda: calls.__setitem__("reset", calls["reset"] + 1),
    )

    with pytest.raises(SystemExit):
        main_mod.main()

    assert calls["release"] == 1
    assert calls["reset"] >= 1
