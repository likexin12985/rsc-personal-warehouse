from pathlib import Path

import migration_runner


def test_runner_wraps_alembic_upgrade_before_script_discovery(monkeypatch, tmp_path):
    config_file = tmp_path / "alembic.ini"
    script_location = tmp_path / "alembic"
    (script_location / "versions").mkdir(parents=True)
    config_file.write_text(
        "[alembic]\n"
        f"script_location = {script_location}\n"
    )
    calls = []

    def fake_upgrade(config, revision, *, sql):
        calls.append((config.config_file_name, revision, sql))

    monkeypatch.setattr(migration_runner.command, "upgrade", fake_upgrade)
    assert migration_runner.main(["--config", str(config_file), "head"]) == 0
    assert calls == [(str(config_file), "head", False)]
