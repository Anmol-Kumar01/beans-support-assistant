import json
from pathlib import Path

import pytest

from evals.catalog import Catalog
from evals.config import EvalSettings


def _write(folder: Path, name: str, data: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(data))


@pytest.fixture
def legacy_dir(tmp_path: Path) -> Path:
    root = tmp_path / "beans-support-bot"
    _write(root / "Article Jsons", "111.json", {
        "id": 111,
        "title": "Drivers: Work Schedule and Time Off Requests",
        "metadata": {"html_url": "https://beansai.zendesk.com/hc/en-us/articles/111-Drivers-Work-Schedule"},
        "body": "Drivers request time off from the Calendar tab in the Hub.",
    })
    _write(root / "Video Jsons", "vid1.json", {
        "id": "vid1",
        "title": "Access and Add Gate Codes",
        "transcript": [{"text": "clicking the info button", "duration": 4.4, "offset": 1.1, "lang": "en"}],
    })
    _write(root / "Video Jsons", "vid1 Part 2.json", {
        "id": "vid1 Part 2",
        "title": "Access and Add Gate Codes Part 2",
        "transcript": [{"text": "shows the gate code &amp;#39;notes&amp;#39;", "duration": 3, "offset": 1, "lang": "en"}],
    })
    _write(root / "Release Notes Jsons", "rn.json", {
        "id": "creating-preset-filters",
        "title": "Creating and Managing Preset Filters",
        "summary": "New preset filters.",
        "body": "Step 1: click the plus icon.",
        "metadata": {"tutorial_url": "https://beans-ai.portal.trainn.co/share/RN1/embed?mode=interactive",
                     "display_option_tags": "fedex", "account_buids": ""},
        "images": [],
    })
    for slug in ("adding-driver-fedex", "adding-driver-regionals"):
        _write(root / "Tutorial Jsons", f"{slug}.json", {
            "id": slug,
            "title": "Adding a Driver in Beans Route",
            "summary": "How to add drivers.",
            "trainn_url": "https://beans-ai.portal.trainn.co/share/SHARED/embed",
            "display_option_tags": "fedex;regionals",
            "account_buids": "",
        })
    return root


@pytest.fixture
def catalog(legacy_dir: Path) -> Catalog:
    return Catalog.from_legacy_dir(legacy_dir)


@pytest.fixture
def settings(tmp_path: Path, legacy_dir: Path) -> EvalSettings:
    return EvalSettings(
        _env_file=None,
        legacy_sources_dir=legacy_dir,
        reports_dir=tmp_path / "reports",
    )
