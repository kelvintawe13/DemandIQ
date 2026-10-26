"""Image-ingestion tests: validation, preprocessing and real OCR extraction.

The parser is covered in ``test_drift_ocr_jobs.py``. These tests pin the layer
in front of it — that a non-image is rejected at the boundary, that a real image
is decoded and measured, and that the local engine (when the system binary is
installed) actually reads a rendered log rather than being stubbed forever.
"""

from __future__ import annotations

import base64
import io

import pytest

from app.services.ingestion.image import (
    ImageValidationError,
    _page_segmentation_modes,
    _prepare_image,
    _preprocess,
    _tesseract_extraction,
    validate_image_upload,
)

_ONE_PIXEL_PNG = base64.b64decode(
    # A real 1x1 PNG. The previous fixture had a corrupt IDAT checksum, which
    # Pillow's verifier rejects; that is a broken test image, not a valid upload.
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)


def test_validate_rejects_non_image_without_decoding_it() -> None:
    with pytest.raises(ImageValidationError, match="does not look like an image"):
        validate_image_upload(b"not an image", "log.jpg")


def test_validate_rejects_empty_content() -> None:
    with pytest.raises(ImageValidationError, match="empty"):
        validate_image_upload(b"", "log.jpg")


def test_validate_accepts_png_and_reports_dimensions() -> None:
    pytest.importorskip("PIL")
    info = validate_image_upload(_ONE_PIXEL_PNG, "log.png")
    assert info.format == "png"
    assert (info.width, info.height) == (1, 1)


def test_page_segmentation_modes_are_deduplicated_and_bounded() -> None:
    modes = _page_segmentation_modes()
    assert modes
    assert len(modes) == len(set(modes))
    assert all(0 <= mode <= 13 for mode in modes)


def test_prepare_image_orients_upscales_and_normalises(monkeypatch) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    # A 40x20 landscape strip is deliberately below the upscale threshold.
    source = Image.new("RGB", (40, 20), "white")
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")

    prepared = _prepare_image(buffer.getvalue())
    assert prepared.mode == "L"
    # Border (16 px each side) plus upscaling make the result larger than the
    # source, even though it started as a tiny thumbnail.
    assert prepared.width > 40
    assert prepared.height > 20


def test_a_box_maps_back_onto_the_photo_the_operator_sees() -> None:
    """Highlights are drawn on the original upload, not on the OCR copy.

    The copy the engine reads is upscaled, straightened and padded, so a box
    reported in its coordinates is meaningless on the photo until every step is
    undone. A dot at a known place in the photo is located in the prepared image
    and must map back to the same place — otherwise the review screen would draw
    its highlight somewhere the value is not.
    """
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    photo = Image.new("L", (240, 180), 255)
    ImageDraw.Draw(photo).ellipse([192, 132, 208, 148], fill=0)  # centred on (200, 140)
    buffer = io.BytesIO()
    photo.save(buffer, format="PNG")

    prepared, transform = _preprocess(buffer.getvalue())
    pixels = prepared.load()
    _, x, y = min(
        (pixels[x, y], x, y) for y in range(prepared.height) for x in range(prepared.width)
    )

    box = transform.box_to_source((x - 2, y - 2, 4, 4))
    assert box is not None
    assert abs(box[0] * 240 + box[2] * 120 - 200) < 10
    assert abs(box[1] * 180 + box[3] * 90 - 140) < 10


def test_visual_rows_rejoin_a_column_wise_read() -> None:
    """A sheet read as two columns must still pair each date with its count.

    Tesseract often returns every date in one block and every count in another.
    A parser that trusts the engine's lines then sees lines with only one field
    and imports nothing at all, so rows are grouped by vertical position instead.
    """
    from app.services.ingestion.image import _visual_rows, _Word

    words = [
        _Word(text="12/01/2026", start=0, end=10, box=(40, 40, 100, 20)),
        _Word(text="13/01/2026", start=0, end=10, box=(40, 100, 100, 20)),
        _Word(text="112", start=0, end=3, box=(300, 42, 40, 20)),
        _Word(text="87", start=0, end=2, box=(300, 102, 30, 20)),
    ]

    rows = _visual_rows(words)
    assert [[word.text for word in row] for row in rows] == [
        ["12/01/2026", "112"],
        ["13/01/2026", "87"],
    ]


def test_visual_rows_follow_a_slanted_page() -> None:
    """A row that drifts down to the right is still one row.

    Deskew straightens the page, but handwriting wanders and a photo can stay a
    degree or two off. On a wide sheet that puts the count column almost a line
    below its date; grouping without following the drift splits every row in two
    and recovers nothing.
    """
    from app.services.ingestion.image import _visual_rows, _Word

    slope = 0.08
    words = []
    for index, (date_text, count_text) in enumerate([("12/01/2026", "112"), ("13/01/2026", "87")]):
        base = 40 + index * 60
        words.append(
            _Word(text=date_text, start=0, end=10, box=(40, int(base + slope * 150), 100, 20))
        )
        words.append(
            _Word(text=count_text, start=0, end=3, box=(300, int(base + slope * 380), 40, 20))
        )

    # Without the drift correction these four words land in four bands.
    rows = _visual_rows(words)
    assert [[word.text for word in row] for row in rows] == [
        ["12/01/2026", "112"],
        ["13/01/2026", "87"],
    ]


def test_visual_rows_separate_rows_written_close_together() -> None:
    # 24 px between rows with 20 px glyphs: they do not overlap, so a loose
    # tolerance would pair day 12's count with day 13's date.
    from app.services.ingestion.image import _visual_rows, _Word

    words = [
        _Word(text="12/01/2026", start=0, end=10, box=(40, 40, 100, 20)),
        _Word(text="112", start=0, end=3, box=(300, 41, 40, 20)),
        _Word(text="13/01/2026", start=0, end=10, box=(40, 64, 100, 20)),
        _Word(text="87", start=0, end=2, box=(300, 66, 30, 20)),
    ]

    rows = _visual_rows(words)
    assert [[word.text for word in row] for row in rows] == [
        ["12/01/2026", "112"],
        ["13/01/2026", "87"],
    ]


def test_visual_rows_do_not_merge_a_neighbouring_line() -> None:
    # The rows above and below are two line-heights apart; a loose tolerance that
    # pulled them together would pair a date with the wrong day's count.
    from app.services.ingestion.image import _visual_rows, _Word

    words = [
        _Word(text="12/01/2026", start=0, end=10, box=(40, 40, 100, 20)),
        _Word(text="112", start=0, end=3, box=(300, 40, 40, 20)),
        _Word(text="13/01/2026", start=0, end=10, box=(40, 80, 100, 20)),
        _Word(text="87", start=0, end=2, box=(300, 80, 30, 20)),
    ]

    assert len(_visual_rows(words)) == 2


def test_a_column_wise_read_is_recovered_as_rows(monkeypatch) -> None:
    """The layout path, wired through the engine, on a page read column by column.

    `image_to_string` returns the two columns one after the other — the case that
    used to produce no rows — while `image_to_data` still knows where each word
    sits. The extraction must come back as pairs, with the geometry attached.
    """
    pytest.importorskip("PIL")
    pytesseract = pytest.importorskip("pytesseract")

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(buffer, format="PNG")

    monkeypatch.setattr(
        pytesseract,
        "image_to_string",
        lambda *a, **k: "12/01/2026\n13/01/2026\n112\n87\n",
    )

    words = [
        ("12/01/2026", (40, 40, 100, 20)),
        ("13/01/2026", (40, 100, 100, 20)),
        ("112", (300, 42, 40, 20)),
        ("87", (300, 102, 30, 20)),
    ]
    blocks = {
        "level": [5] * len(words),
        "block_num": [0] * len(words),
        "par_num": [0] * len(words),
        # One engine line per column: the read that defeats a line-based parser.
        "line_num": [0, 1, 2, 3],
        "conf": ["95"] * len(words),
        "text": [word for word, _ in words],
        "left": [box[0] for _, box in words],
        "top": [box[1] for _, box in words],
        "width": [box[2] for _, box in words],
        "height": [box[3] for _, box in words],
    }
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: blocks)
    monkeypatch.setattr("app.services.ingestion.image._page_segmentation_modes", lambda: [6])

    extraction = _tesseract_extraction(buffer.getvalue(), "eng")

    assert [(row["date"], row["demand_qty"]) for row in extraction.rows] == [
        ("2026-01-12", 112),
        ("2026-01-13", 87),
    ]
    assert extraction.quality["row_source"] == "cells"
    assert extraction.rows[0]["boxes"]["demand_qty"] is not None


def test_row_confidence_is_the_parsers_reading_not_the_engines_score(monkeypatch) -> None:
    """Two different numbers, kept apart on purpose.

    A row's `confidence` says how ambiguous the *line* was: 0.75 means several
    numbers were candidates and the nearest to the date was chosen. Tesseract's
    word confidence says how sure the recognizer was of the glyphs. Neither is a
    probability that the record is correct, so mixing them — or presenting one as
    the other — would misrepresent both. This pins them as separate fields.
    """
    pytest.importorskip("PIL")
    pytesseract = pytest.importorskip("pytesseract")

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(buffer, format="PNG")

    monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "12/01/2026   112   6\n")
    words = [
        ("12/01/2026", (40, 40, 100, 20)),
        ("112", (200, 40, 40, 20)),
        ("6", (260, 40, 20, 20)),
    ]
    blocks = {
        "level": [5] * len(words),
        "block_num": [0] * len(words),
        "par_num": [0] * len(words),
        "line_num": [0] * len(words),
        "conf": ["99"] * len(words),
        "text": [word for word, _ in words],
        "left": [box[0] for _, box in words],
        "top": [box[1] for _, box in words],
        "width": [box[2] for _, box in words],
        "height": [box[3] for _, box in words],
    }
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: blocks)
    monkeypatch.setattr("app.services.ingestion.image._page_segmentation_modes", lambda: [6])

    extraction = _tesseract_extraction(buffer.getvalue(), "eng")

    row = extraction.rows[0]
    assert row["demand_qty"] == 112
    # The line was ambiguous (112 or 6), whatever the recognizer thought of it.
    assert row["confidence"] == 0.75
    # The engine's own score lives in quality, never in the row.
    assert extraction.quality["mean_confidence"] == 0.99
    assert row["confidence"] != extraction.quality["mean_confidence"]


def test_real_tesseract_reads_a_rendered_log() -> None:
    """The local path must be exercised, not only monkeypatched.

    CI without the OCR extra skips this. The Docker image built with
    ``BACKEND_EXTRAS=ocr`` installs both pytesseract and the Tesseract binary, so
    this is the test that proves the advertised path works there.
    """
    pytest.importorskip("PIL")
    pytesseract = pytest.importorskip("pytesseract")

    from PIL import Image, ImageDraw, ImageFont

    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - depends on host packages
        pytest.skip(f"Tesseract binary unavailable: {exc}")

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 48)
    except OSError:
        font = ImageFont.load_default()

    image = Image.new("RGB", (1200, 420), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 60), "12/01/2026   112", fill="black", font=font)
    draw.text((40, 200), "13/01/2026   87", fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    extraction = _tesseract_extraction(buffer.getvalue(), "eng")

    assert extraction.engine == "tesseract"
    assert extraction.rows, "the rendered log produced no OCR rows"
    assert any(row["demand_qty"] in (112, 87) for row in extraction.rows)
    assert extraction.quality.get("page_segmentation_mode") in _page_segmentation_modes()
    # This page reads as normal rows, so the layout reconstruction must not take
    # over: it is a fallback for pages the lines cannot carry, not a replacement.
    assert extraction.quality.get("row_source") == "lines"

    # The review screen highlights each value on the photo, so a row has to
    # carry where it was read. Fractions of the upload, in reading order: the
    # date sits left of its count on this page.
    row = next(row for row in extraction.rows if row["demand_qty"] in (112, 87))
    date_box = row["boxes"]["date"]
    count_box = row["boxes"]["demand_qty"]
    assert date_box is not None and count_box is not None
    for box in (date_box, count_box):
        left, top, width, height = box
        assert 0.0 <= left < 1.0 and 0.0 <= top < 1.0
        assert 0.0 < width <= 1.0 and 0.0 < height <= 1.0
    assert date_box[0] < count_box[0]
