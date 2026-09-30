from __future__ import annotations

from pathlib import Path

from app.portal.config import CONFIG_DIR, load_config


def test_empty_sections_keep_defaults(tmp_path: Path) -> None:
    (tmp_path / "default.yaml").write_text("search:\n  max_pages: 2\n")
    # every selector commented out → YAML gives None for the section
    (tmp_path / "acme.yaml").write_text("login:\n  # username: '#u'\njob:\napply:\n")
    cfg = load_config("Acme", root=tmp_path)
    assert cfg.source == "acme.yaml"
    assert cfg.search.max_pages == 2
    assert not cfg.apply.ready


def test_shipped_configs_load() -> None:
    for path in CONFIG_DIR.glob("*.yaml"):
        load_config(path.stem)
