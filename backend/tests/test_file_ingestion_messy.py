"""Messy files are the normal case for a paper log keyed into Excel.

These cover the three ways the first version lost every row of a real file: the
header was assumed to be row 1 (so a title line became the only column), cells
were read as if they were clean (`1,112`, `Dec 3, 2026`, `n/a`), and a single
footer line made the whole import uncommittable.
"""

from __future__ import annotations

import datetime

import pytest

from app.services.ingestion.file import (
    apply_column_map,
    detect_header_row,
    header_field,
    normalise_header,
    parse_date_value,
    parse_file_table,
    parse_int_value,
    suggest_column_map,
)

# A logbook page keyed into Excel by someone who was not thinking about parsers:
# a title, a date range, a blank spacer row, decorated headers, thousands
# separators, three different date spellings, a total, and an "n/a" week.
MESSY = (
    "Kigali Heights Bistro - visitor log\n"
    "Week of 1 Dec 2025\n"
    ",,\n"
    "Date,Visitors (adults),Location,Notes\n"
    '01/12/2025,"1,112",Kigali Heights Bistro,weekend rush\n'
    "2025-12-02,987,Kigali Heights Bistro,\n"
    "Dec 3, 2025,1 034,Kigali Heights Bistro,holiday eve\n"
    "3/12/25,998,Kigali Heights,\n"
    ",,\n"
    ",,,Checked by Alice\n"
    "TOTAL,4131,,\n"
    "N/A,-,,\n"
)


def parsed():
    return parse_file_table("messy.csv", MESSY.encode())


class TestHeaderDetection:
    def test_finds_the_table_below_a_title_and_a_date_range(self) -> None:
        table = parsed()
        assert table.header_row == 3
        assert table.preamble_rows == 3
        assert table.headers == ["Date", "Visitors (adults)", "Location", "Notes"]

    def test_a_data_row_is_never_mistaken_for_the_header(self) -> None:
        # The first row of *data* names a date; a header names fields.
        matrix = [["01/12/2025", "112", "Kigali"], ["Date", "Visitors", "Location"]]
        assert detect_header_row(matrix) == 1

    def test_a_title_above_a_date_and_visits_header_is_preamble(self) -> None:
        """The word "Visits" has to count as a field name.

        It did not, so `date,visits` named only one canonical field — no row
        qualified as a header, the fallback took the restaurant's title line as
        the column name, and the whole table collapsed into one column. Found by
        uploading a real log through the route, not by reading the aliases.
        """
        table = parse_file_table(
            "log.csv",
            b"Remera Grills visitor log\n\ndate,visits\n05/08/2026,51\nTOTAL,156\n",
        )
        assert table.header_row == 2
        assert table.headers == ["date", "visits"]
        assert table.rows[0] == {"date": "05/08/2026", "visits": "51"}

    def test_unfamiliar_names_fall_back_to_the_first_row(self) -> None:
        # A file we cannot recognise keeps the old behaviour rather than
        # silently dropping its first line.
        table = parse_file_table("x.csv", b"alpha,beta\n1,2\n")
        assert table.header_row == 0
        assert table.headers == ["alpha", "beta"]

    def test_duplicate_and_blank_headers_stay_addressable(self) -> None:
        table = parse_file_table("d.csv", b"Date,Visitors,Visitors,\n2025-12-01,1,2,3\n")
        assert table.headers == ["Date", "Visitors", "Visitors (2)", "column_4"]
        assert table.rows[0]["Visitors (2)"] == "2"

    def test_short_rows_are_padded_rather_than_dropped(self) -> None:
        table = parse_file_table("r.csv", b"Date,Visitors,Notes\n2025-12-01,40\n")
        assert table.rows[0]["Notes"] == ""


class TestHeaderAliases:
    def test_units_and_words_are_ignored_when_matching(self) -> None:
        assert normalise_header("Visitors (adults)") == "visitors"
        assert header_field("Visitors (adults)") == "demand_qty"
        assert header_field("No. of guests") == "demand_qty"
        assert header_field("Service Date") == "date"
        assert header_field("Covers") == "demand_qty"

    def test_a_column_is_named_after_the_word_it_starts_with(self) -> None:
        # "Comment on demand" is a note about demand, not a demand column.
        assert header_field("Comment on demand") == "notes"

    def test_update_is_not_a_date_column(self) -> None:
        assert header_field("updated_by") is None

    def test_visits_is_a_demand_column(self) -> None:
        for header in ("Visits", "Visit", "No. of visits", "Visits (adults)"):
            assert header_field(header) == "demand_qty", header
        assert suggest_column_map(["date", "visits"]) == {
            "date": "date",
            "demand_qty": "visits",
        }

    def test_suggests_a_full_map_from_decorated_headers(self) -> None:
        assert suggest_column_map(["Date", "Visitors (adults)", "Location", "Notes"]) == {
            "date": "Date",
            "demand_qty": "Visitors (adults)",
            "location": "Location",
            "notes": "Notes",
        }

    def test_never_suggests_the_same_header_for_two_fields(self) -> None:
        mapped = suggest_column_map(["Date", "Visitors", "Visitors", "Location"])
        assert mapped["demand_qty"] in {"Visitors", "Visitors (2)"}
        assert len(set(mapped.values())) == len(mapped.values())


class TestMessyRows:
    def test_maps_every_real_day_and_skips_the_furniture(self) -> None:
        table = parsed()
        mapping = suggest_column_map(table.headers)
        mapped, errors, skipped = apply_column_map(table.rows, mapping)

        assert errors == []
        assert [row["demand_qty"] for row in mapped] == [1112, 987, 1034, 998]
        assert [row["date"].isoformat() for row in mapped] == [
            "2025-12-01",
            "2025-12-02",
            "2025-12-03",
            "2025-12-03",
        ]
        # The signature line and the TOTAL footer are not data, and are named as
        # skipped rather than as rows the operator must repair. The "N/A,-" row
        # carries no information at all, so it never becomes a row.
        assert [row["reason"] for row in skipped] == ["no_date_or_count", "summary_row"]

    def test_a_clean_file_still_reports_no_skips(self) -> None:
        table = parse_file_table("c.csv", b"date,covers\n2025-12-01,40\n")
        mapped, errors, skipped = apply_column_map(
            table.rows, {"date": "date", "demand_qty": "covers"}
        )
        assert len(mapped) == 1 and errors == [] and skipped == []


class TestValueTolerance:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2025-12-03", datetime.date(2025, 12, 3)),
            ("03/12/2025", datetime.date(2025, 12, 3)),
            ("12/03/2025", datetime.date(2025, 3, 12)),  # day-first reading
            ("31/12/2025", datetime.date(2025, 12, 31)),
            ("12/31/2025", datetime.date(2025, 12, 31)),  # only valid month-first
            ("3/12/25", datetime.date(2025, 12, 3)),
            ("2025.12.03", datetime.date(2025, 12, 3)),
            ("Dec 3, 2025", datetime.date(2025, 12, 3)),
            ("3 Dec 2025", datetime.date(2025, 12, 3)),
            ("3rd Dec 2025", datetime.date(2025, 12, 3)),
            ("3 December 2025", datetime.date(2025, 12, 3)),
            ("2025-12-03 14:05:00", datetime.date(2025, 12, 3)),
            (45994, datetime.date(2025, 12, 3)),  # Excel serial
            ("45994", datetime.date(2025, 12, 3)),  # serial exported as text
        ],
    )
    def test_dates(self, raw: object, expected: datetime.date) -> None:
        assert parse_date_value(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1,234", 1234),
            ("1 034", 1034),
            ("1.234", 1234),  # European thousands, not one-and-a-bit
            ("RWF 1,234", 1234),
            ("$1,234.00", 1234),
            ("12.5", 12),
            (112, 112),
            (112.0, 112),
        ],
    )
    def test_counts(self, raw: object, expected: int) -> None:
        assert parse_int_value(raw) == expected

    @pytest.mark.parametrize("raw", ["N/A", "", "-", "abc", -5])
    def test_counts_that_are_not_counts(self, raw: object) -> None:
        with pytest.raises(ValueError):
            parse_int_value(raw)

    def test_a_blank_or_text_date_is_not_a_date(self) -> None:
        for raw in ("", "N/A", "Week of 1 Dec", "TOTAL"):
            with pytest.raises(ValueError):
                parse_date_value(raw)
