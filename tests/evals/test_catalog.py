import pytest

from evals.config import EvalSettings
from evals.catalog import Catalog

REAL_SOURCES_DIR = EvalSettings().sources_dir  # EVAL_SOURCES_DIR in .env


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


def test_audience_fields(catalog):
    doc = catalog.documents["trainn:adding-driver-fedex"]
    assert doc.audience_tags == ["fedex", "regionals"]


@pytest.mark.skipif(not REAL_SOURCES_DIR.is_dir(), reason="data_sources/ not present")
def test_real_corpus_loads():
    cat = Catalog.from_dir(REAL_SOURCES_DIR)
    # 182 source items; the YouTube "Part 2" file merges into its parent video.
    assert len(cat) == 181
    assert "zendesk:11470853429911" in cat.documents
