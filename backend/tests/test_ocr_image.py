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
    _extract_via_vision,
    _find_date,
    _merge_reads,
    _page_segmentation_modes,
    _prepare_image,
    _preprocess,
    _Read,
    _tesseract_extraction,
    extract_rows_with_engine,
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


def test_a_letter_read_as_a_day_does_not_discard_the_page() -> None:
    """One unreadable glyph must not cost the whole photo.

    Tesseract reads a handwritten 1 as `l`, `I`, `|` or `L`. The month-first
    pattern matches all four (it is case-insensitive), but the substitution only
    covered the lower-case forms, so `Oct L` raised ValueError — and that
    escaped the page, so a twenty-one-row photo came back with no rows at all.
    """
    date, span = _find_date("blank sheet spilled Oct L", day_first=True, reporting_year=2026)
    assert date is not None and date.isoformat() == "2026-10-01"
    assert span is not None

    # Still no date when the glyph is genuinely not a digit, but no exception.
    assert _find_date("Oct Z 12", day_first=True, reporting_year=2026)[0] is None


def _read(date: str, qty: int, *, psm: int = 6, conf: float = 0.6, variant: str = "photo"):
    row = {
        "row_index": 0,
        "date": date,
        "demand_qty": qty,
        "location": None,
        "notes": None,
        "confidence": 0.9,
        "boxes": {"date": None, "demand_qty": None},
    }
    return _Read(rows=[row], source="lines", variant=variant, psm=psm, mean_confidence=conf)


def test_reads_of_different_modes_are_merged_not_ranked() -> None:
    """Two modes that read different halves of a page both contribute.

    Keeping only the best-scoring extraction was the bug: on a measured page of
    twenty-one hand-written days, block mode recovered days 1-3 and 8 while
    sparse mode recovered 4, 5 and 12-15 — nearly disjoint sets — and the other
    half was thrown away even though it had already been read correctly.
    """
    reads = [
        _read("2026-10-01", 34, psm=6),
        _read("2026-10-02", 28, psm=6),
        _read("2026-10-04", 50, psm=11, conf=0.77),
        _read("2026-10-05", 32, psm=11, conf=0.77),
    ]

    rows, disagreements = _merge_reads(reads)

    assert [(row["date"], row["demand_qty"]) for row in rows] == [
        ("2026-10-01", 34),
        ("2026-10-02", 28),
        ("2026-10-04", 50),
        ("2026-10-05", 32),
    ]
    assert disagreements == 0
    # Reading order is preserved so the review list follows the page.
    assert [row["row_index"] for row in rows] == [0, 1, 2, 3]


def test_agreeing_reads_outvote_a_single_dissenting_one() -> None:
    """The value most reads agree on wins, not the first one seen."""
    reads = [
        _read("2026-10-13", 40, psm=6, conf=0.55),
        _read("2026-10-13", 40, psm=11, conf=0.77, variant="binarised"),
        _read("2026-10-13", 34, psm=4, conf=0.90),
    ]

    rows, disagreements = _merge_reads(reads)

    assert [(row["date"], row["demand_qty"]) for row in rows] == [("2026-10-13", 40)]
    # The screen is told a day was argued over: that is the row worth re-reading.
    assert disagreements == 1


def test_a_tie_goes_to_the_more_confident_read() -> None:
    """One read each way is settled by how well each read saw the page."""
    reads = [
        _read("2026-10-05", 29, psm=6, conf=0.55),
        _read("2026-10-05", 32, psm=11, conf=0.77),
    ]

    rows, _ = _merge_reads(reads)

    assert rows[0]["demand_qty"] == 32


def test_one_read_counts_once_per_date() -> None:
    """A mode that split one day into two rows does not outvote the others."""
    split = _read("2026-10-13", 34, psm=11, conf=0.77)
    split = _Read(
        rows=[*split.rows, {**split.rows[0], "demand_qty": 40, "row_index": 1}],
        source=split.source,
        variant=split.variant,
        psm=split.psm,
        mean_confidence=split.mean_confidence,
    )
    reads = [split, _read("2026-10-13", 40, psm=6, conf=0.55)]

    rows, _ = _merge_reads(reads)

    # 34 and 40 hold one vote each, so the tie-break decides it and the more
    # confident read wins. Counting the duplicate would have handed 40 two votes
    # and the election — one read cannot outvote itself.
    assert [row["demand_qty"] for row in rows] == [34]


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_vision_request_carries_the_photo_and_asks_for_json(monkeypatch) -> None:
    """The vision call is the OpenAI-shaped one, with the year in the prompt.

    Handwriting is the case the local engine cannot read, so this request is the
    one that has to work on a real provider. Pinned here: the image travels as a
    base64 data URL (not multipart, which is the *custom* service's shape), the
    model and key are passed through, and the yearless-date instruction is the
    operator's reporting year rather than the current one.
    """
    captured: dict = {}

    def fake_post(url, *, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, body=json, timeout=timeout)
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"rows": [{"date": "2026-10-01",'
                            ' "demand_qty": 34, "confidence": 0.93}]}\n```'
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("app.services.ingestion.image.httpx.post", fake_post)

    rows = _extract_via_vision(
        _ONE_PIXEL_PNG,
        url="http://vision.test/v1/chat/completions",
        api_key="secret-key",
        model="a-vision-model",
        timeout=42.0,
        reporting_year=2026,
    )

    assert captured["url"] == "http://vision.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert captured["timeout"] == 42.0
    assert captured["body"]["model"] == "a-vision-model"
    prompt = captured["body"]["messages"][0]["content"]
    assert "2026" in prompt
    # The photo is one message's image part, inline rather than multipart.
    image_part = captured["body"]["messages"][1]["content"][1]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")

    # A fenced JSON reply is a formatting habit, not a failed read.
    assert [(row["date"], row["demand_qty"]) for row in rows] == [("2026-10-01", 34)]
    assert rows[0]["confidence"] == 0.93
    # One model read means no merge, so no agreement is invented for it.
    assert "agreement" not in rows[0] and "readers" not in rows[0]


def test_a_vision_reply_that_is_not_json_fails_loudly(monkeypatch) -> None:
    """The caller must be able to fall back and say why, not get silent zeroes."""
    monkeypatch.setattr(
        "app.services.ingestion.image.httpx.post",
        lambda *a, **k: _FakeResponse(
            {"choices": [{"message": {"content": "I cannot read this."}}]}
        ),
    )

    with pytest.raises(RuntimeError, match="not JSON"):
        _extract_via_vision(
            _ONE_PIXEL_PNG,
            url="http://vision.test/v1/chat/completions",
            api_key=None,
            model="m",
            timeout=5.0,
        )


def test_a_configured_vision_model_is_used_before_the_local_engine(monkeypatch) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ocr_service_url", None)
    monkeypatch.setattr(settings, "ocr_vision_url", "http://vision.test/v1/chat/completions")
    monkeypatch.setattr(settings, "ocr_vision_model", "a-vision-model")
    monkeypatch.setattr(
        "app.services.ingestion.image.httpx.post",
        lambda *a, **k: _FakeResponse(
            {
                "choices": [
                    {"message": {"content": '{"rows": [{"date": "2026-10-01", "demand_qty": 34}]}'}}
                ]
            }
        ),
    )

    extraction = extract_rows_with_engine(_ONE_PIXEL_PNG, "log.png", reporting_year=2026)

    assert extraction.engine == "remote"
    assert extraction.quality["provider"] == "vision"
    assert [row["demand_qty"] for row in extraction.rows] == [34]


def test_a_vision_read_that_finds_nothing_falls_back_to_the_local_engine(monkeypatch) -> None:
    """An empty model answer is a failed read, not an empty page.

    Reporting zero rows on the strength of one refusal would tell the operator
    there is nothing on the photo while the local engine might have read most of
    it, so the fallback still runs.
    """
    pytest.importorskip("PIL")
    from app.config import get_settings
    from app.services.ingestion import image as image_module

    settings = get_settings()
    monkeypatch.setattr(settings, "ocr_service_url", None)
    monkeypatch.setattr(settings, "ocr_vision_url", "http://vision.test/v1/chat/completions")
    monkeypatch.setattr(
        "app.services.ingestion.image.httpx.post",
        lambda *a, **k: _FakeResponse({"choices": [{"message": {"content": '{"rows": []}'}}]}),
    )

    local = image_module.ImageExtraction(
        rows=[
            {
                "row_index": 0,
                "date": "2026-10-02",
                "demand_qty": 28,
                "location": None,
                "notes": None,
                "confidence": 0.9,
                "boxes": {"date": None, "demand_qty": None},
            }
        ],
        engine="tesseract",
        quality={"rows": 1},
    )
    monkeypatch.setattr(image_module, "_tesseract_extraction", lambda *a, **k: local)

    extraction = extract_rows_with_engine(_ONE_PIXEL_PNG, "log.png", reporting_year=2026)

    assert extraction.engine == "tesseract"
    assert [row["demand_qty"] for row in extraction.rows] == [28]


def test_a_disputed_row_carries_its_vote_split() -> None:
    """The row says how much of the evidence backed it, not just that it won.

    The review screen lists these first, so the counts have to be the real ones:
    three reads found the day, two produced the figure kept.
    """
    reads = [
        _read("2026-10-15", 24, psm=6, conf=0.55),
        _read("2026-10-15", 24, psm=11, conf=0.77, variant="binarised"),
        _read("2026-10-15", 29, psm=4, conf=0.60),
    ]

    rows, _ = _merge_reads(reads)

    assert rows[0]["readers"] == 3
    assert rows[0]["agreement"] == 0.67


def test_an_agreed_row_reports_unanimity() -> None:
    reads = [
        _read("2026-10-02", 28, psm=6, conf=0.55),
        _read("2026-10-02", 28, psm=11, conf=0.77),
    ]

    rows, disagreements = _merge_reads(reads)

    assert (rows[0]["readers"], rows[0]["agreement"]) == (2, 1.0)
    assert disagreements == 0


def test_a_trailing_seven_read_as_a_letter_is_still_a_seven() -> None:
    """`27` read back as `2F` must import 27, not 2.

    On a measured hand-written page the day of 27 visitors was transcribed as
    `2F`: the engine read the 7's top bar as a letter. The parser took the digit
    it could see and staged that day as 2 visitors — a row the operator has no
    reason to question, because nothing about it looks wrong.
    """
    from app.services.ingestion.image import parse_ocr_text

    rows = parse_ocr_text("8 Oct  2F", day_first=True, reporting_year=2026)

    assert [(row["date"], row["demand_qty"]) for row in rows] == [("2026-10-08", 27)]
    # A correction is a guess enumerated for the operator, like an ambiguous line.
    assert rows[0]["confidence"] == 0.75

    # Not a licence to glue an F onto a digit mid-token or to a real unit.
    assert parse_ocr_text("8 Oct  F2", day_first=True, reporting_year=2026)[0]["demand_qty"] == 2
