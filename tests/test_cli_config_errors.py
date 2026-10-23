"""A bad --config (missing file, invalid YAML, schema failure) should print
one clean `error: ...` line and exit 1 from run/inspect/demo -- no traceback."""

from __future__ import annotations

import argparse

import pytest

from legacy2mcp.cli import _cmd_demo, _cmd_inspect, _cmd_run
from legacy2mcp.config import ConfigError, load_config


def _run_args(config: str) -> argparse.Namespace:
    return argparse.Namespace(config=config, log_level="warning")


def _inspect_args(config: str) -> argparse.Namespace:
    return argparse.Namespace(config=config)


def _demo_args(config: str) -> argparse.Namespace:
    return argparse.Namespace(
        from_contract=None,
        config=config,
        out="./chat.html",
        write_config="./config.yaml",
        no_write_config=False,
        host="127.0.0.1",
        port=0,
        no_open=True,
    )


COMMANDS = [
    pytest.param(_cmd_run, _run_args, id="run"),
    pytest.param(_cmd_inspect, _inspect_args, id="inspect"),
    pytest.param(_cmd_demo, _demo_args, id="demo"),
]


def _bad_configs(tmp_path):
    invalid_yaml = tmp_path / "broken.yaml"
    invalid_yaml.write_text("server:\n  name: [unclosed\n", encoding="utf-8")
    bad_schema = tmp_path / "bad-schema.yaml"
    bad_schema.write_text("adapters:\n  - id: legacy\n    type: carrier-pigeon\n", encoding="utf-8")
    return {
        "missing": (str(tmp_path / "does-not-exist.yaml"), "config file not found"),
        "invalid_yaml": (str(invalid_yaml), "is not valid YAML"),
        "bad_schema": (str(bad_schema), "is invalid"),
    }


@pytest.mark.parametrize("case", ["missing", "invalid_yaml", "bad_schema"])
@pytest.mark.parametrize("cmd, make_args", COMMANDS)
def test_bad_config_prints_clean_error(cmd, make_args, case, tmp_path, capsys):
    path, expected = _bad_configs(tmp_path)[case]

    with pytest.raises(SystemExit) as excinfo:
        cmd(make_args(path))

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert expected in err
    assert path in err
    assert "Traceback" not in err
    assert err.count("\n") == 1  # one line


def test_load_config_names_the_bad_field(tmp_path):
    bad_schema = tmp_path / "bad-schema.yaml"
    bad_schema.write_text("adapters:\n  - id: legacy\n    type: carrier-pigeon\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(str(bad_schema))

    assert "adapters.0.type" in str(excinfo.value)


def test_load_config_reports_yaml_position(tmp_path):
    broken = tmp_path / "broken.yaml"
    broken.write_text("server:\n  name: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(str(broken))

    assert "line " in str(excinfo.value)


def test_load_config_still_loads_a_good_file(tmp_path):
    good = tmp_path / "config.yaml"
    good.write_text("server:\n  name: ok\nadapters: []\n", encoding="utf-8")

    assert load_config(str(good)).server.name == "ok"
