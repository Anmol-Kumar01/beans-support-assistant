from pathlib import Path

import pytest

from evals.config import EvalSettings
from evals.catalog import Catalog, normalize_url

REAL_LEGACY_DIR = EvalSettings().legacy_sources_dir  # EVAL_LEGACY_SOURCES_DIR in .env


def test_ids_and_part2_merge(catalog):
    assert set(catalog.documents) == {
        "zendesk:111",
        "youtube:vid1",
        "release_note:creating-preset-filters",
        "trainn:adding-driver-fedex",
        "trainn:adding-driver-regionals",
    }
    video = catalog.documents["youtube:vid1"]
    assert "clicking the info button" in video.text and "'notes'" in video.text


def test_resolve_url_variants(catalog):
    assert catalog.resolve_url("https://beansai.zendesk.com/hc/en-us/articles/111-Anything") == ["zendesk:111"]
    assert catalog.resolve_url("https://www.youtube.com/watch?v=vid1&t=61s") == ["youtube:vid1"]
    assert catalog.resolve_url("https://youtu.be/vid1") == ["youtube:vid1"]
    assert catalog.resolve_url("https://beans-ai.portal.trainn.co/share/RN1/embed") == [
        "release_note:creating-preset-filters"
    ]
    # Two tutorials share one Trainn URL: both are returned.
    assert catalog.resolve_url("https://beans-ai.portal.trainn.co/share/SHARED/embed?mode=interactive") == [
        "trainn:adding-driver-fedex",
        "trainn:adding-driver-regionals",
    ]
    assert catalog.resolve_url("https://beansai.zendesk.com/hc/en-us/articles/999") == []
    assert normalize_url("https://beansroute.ai") is None


def test_resolve_title_is_normalized(catalog):
    assert catalog.resolve_title("  creating and managing preset filters. ") == [
        "release_note:creating-preset-filters"
    ]


def test_audience_fields(catalog):
    doc = catalog.documents["trainn:adding-driver-fedex"]
    assert doc.audience_tags == ["fedex", "regionals"]


@pytest.mark.skipif(not REAL_LEGACY_DIR.is_dir(), reason="legacy repo not present")
def test_real_legacy_corpus_loads():
    cat = Catalog.from_legacy_dir(REAL_LEGACY_DIR)
    # 182 source items; the YouTube "Part 2" file merges into its parent video.
    assert len(cat) == 181
    assert cat.resolve_url(
        "https://beansai.zendesk.com/hc/en-us/articles/11470853429911-Drivers-Work-Schedule-and-Time-Off-Requests"
    ) == ["zendesk:11470853429911"]
