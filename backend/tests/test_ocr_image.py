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
