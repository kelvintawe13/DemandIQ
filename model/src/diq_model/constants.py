"""Shared constants: dataset file names and audited figures.

Numbers here are quoted from Sections 3.4.1 and 3.4.2 of the research
proposal so the audits can fail loudly if reality differs from the
documented expectation.
"""

from __future__ import annotations

# Kaggle competition slug (used by loaders when pulling via kagglehub/API).
KAGGLE_COMPETITION = "recruit-restaurant-visitor-forecasting"

# Canonical file names inside the Recruit archive.
VISIT_FILE = "air_visit_data.csv"
STORE_FILE = "air_store_info.csv"
CALENDAR_FILE = "date_info.csv"
RESERVE_FILE = "air_reserve.csv"

# Audited figures (proposal Section 3.4.1 / 3.7.1).
EXPECTED_VISIT_ROWS = 252_108
EXPECTED_STORES = 829
VISIT_DATE_MIN = "2016-01-01"
VISIT_DATE_MAX = "2017-04-22"

# Reservation coverage: only 314 of the 821 forecastable restaurants have logs.
RESERVE_RESTAURANTS = 314

# Audited figures (proposal Section 3.4.2 / 3.7.2) — URBAN-WASTE FTD,
# Eriksson & Lindgren (2019), Zenodo DOI 10.5281/zenodo.3355300, CC BY 4.0.
URBAN_WASTE_ROWS = 3_321
URBAN_WASTE_ESTABLISHMENTS = 34
URBAN_WASTE_PILOT_LOCATIONS = 6
URBAN_WASTE_NONMISSING_WASTE = 3_160
URBAN_WASTE_WASTE_AND_ORDERED = 9
URBAN_WASTE_DOI = "10.5281/zenodo.3355300"
