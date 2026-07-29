"""Test script for Venue Cymru extractor."""

import json
import os
import sys
import unittest

from scrapers.venue_cymru.run_extractor import VenueCymruExtractor
from utils.csv_validator import COLUMN_ORDER

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestVenueCymruExtractor(unittest.TestCase):
    def setUp(self):
        self.extractor = VenueCymruExtractor(
            local_test=True,
            show_count=2,
            save_csv_locally=True,
            csv_incremental_mode=False,
        )

    def test_resolve_pagination_url(self):
        """Drupal pager hrefs are turned into absolute URLs."""
        self.assertEqual(
            self.extractor._resolve_pagination_url("?page=1"),
            "https://www.venuecymru.co.uk/whats-on?page=1",
        )
        self.assertEqual(
            self.extractor._resolve_pagination_url("/whats-on?page=2"),
            "https://www.venuecymru.co.uk/whats-on?page=2",
        )
        self.assertEqual(
            self.extractor._resolve_pagination_url(
                "https://www.venuecymru.co.uk/whats-on?page=3"
            ),
            "https://www.venuecymru.co.uk/whats-on?page=3",
        )

    def test_is_blocked_seat(self):
        """Blocked seats are identified by the blocked flag or status."""
        self.assertTrue(self.extractor._is_blocked_seat({"blocked": True}))
        self.assertTrue(self.extractor._is_blocked_seat({"status": "blocked"}))
        self.assertFalse(self.extractor._is_blocked_seat({"status": "available"}))
        self.assertFalse(self.extractor._is_blocked_seat({}))

    def test_extraction_and_transformation(self):
        """Run a small live extraction and validate the transformed output."""
        raw = self.extractor.extract()
        self.assertIsNotNone(raw)

        raw_dataframe = self.extractor._parse(raw)
        self.assertFalse(raw_dataframe.empty, "Extracted raw data is empty")

        clean_dataframe = self.extractor._transform_to_uniform_schema(raw_dataframe)
        self.assertFalse(clean_dataframe.empty, "Cleaned data is empty")

        for column in COLUMN_ORDER:
            self.assertIn(column, clean_dataframe.columns)

        excluded_columns = ["uuid", "seat_id", "people"]
        for column in excluded_columns:
            self.assertNotIn(column, clean_dataframe.columns)

        optional_fields = {
            "booking_start_date",
            "booking_end_date",
            "open_date",
            "close_date",
            "is_limited_run",
            "capacity",
        }

        for _, row in clean_dataframe.iterrows():
            for column, value in row.items():
                if column in optional_fields:
                    continue
                self.assertNotEqual(
                    str(value).lower(),
                    "none",
                    f"Literal 'None' found in column {column}",
                )

            self.assertEqual(row["currency"], "GBP", "Currency should be GBP")

            if row["open_date"] is not None:
                self.assertRegex(row["open_date"], r"^\d{4}-\d{2}-\d{2}$")
            if row["close_date"] is not None:
                self.assertRegex(row["close_date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertRegex(row["scrape_datetime"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

            performances_raw = row["upcoming_performances"]
            if performances_raw is not None:
                performances = (
                    performances_raw
                    if isinstance(performances_raw, list)
                    else json.loads(performances_raw.replace("'", '"'))
                )
                self.assertIsInstance(performances, list)
                if performances:
                    self.assertIn("date", performances[0])
                    self.assertIn("time", performances[0])

            seat_pricing_raw = row["seat_pricing"]
            if seat_pricing_raw:
                seat_pricing = (
                    seat_pricing_raw
                    if isinstance(seat_pricing_raw, dict)
                    else json.loads(seat_pricing_raw.replace("'", '"'))
                )
                self.assertIsInstance(seat_pricing, dict)


if __name__ == "__main__":
    unittest.main()
