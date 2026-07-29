"""Venue Cymru extractor."""

import json
import re
import sys
from datetime import date
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

from utils.base_extractor import BaseExtractor
from utils.logger import setup_logger
from utils.scraping_helpers import (
    format_datetime_key,
    get_scrape_datetime,
    human_delay,
    normalize_country,
    standardize_category,
)

from .venue_cymru_config import (
    ALLOWED_GENRES,
    API_BASE,
    BASE_URL,
    CURRENCY_CODE,
    DELAY_BETWEEN_CALLS,
    EVENT_INCLUDE,
    GENRE_CATEGORY_MAP,
    HEADERS,
    MAX_PAGES,
    REQUEST_TIMEOUT,
    TICKETSOLVE_BASE,
    VENUE_DEFAULTS,
    WHATS_ON_URL,
)

logger = setup_logger(__name__, log_to_file=False)

_SHOW_ID_RE = re.compile(r"/shows/(\d+)/events")


class VenueCymruExtractor(BaseExtractor):
    def __init__(self, local_test=False, show_count=None, **kwargs):
        super().__init__(
            site_id="venue_cymru",
            local_test=local_test,
            show_count=show_count,
            **kwargs,
        )
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def extract(self) -> bytes:
        """Scrape public listing cards and return them as JSON bytes."""
        self._establish_session()
        human_delay(*DELAY_BETWEEN_CALLS)
        cards = self._scrape_all_cards()
        self.custom_logger.info(f"Listing: {len(cards)} show card(s) found")
        if self.local_test and self.show_count:
            cards = cards[: self.show_count]
            self.custom_logger.info(f"LOCAL TEST: capped to {len(cards)} shows")
        return json.dumps(cards, default=str).encode("utf-8")

    def _parse(self, raw: bytes) -> pd.DataFrame:
        """Turn raw listing cards into parsed venue-cymru rows."""
        cards = json.loads(raw.decode("utf-8"))
        rows: list[dict[str, Any]] = []
        today_iso = date.today().isoformat()

        for card in cards:
            show_id = card.get("show_id")
            title = card.get("title", "")
            if not show_id or not title:
                self.custom_logger.warning(
                    f"Skipping card without show_id/title: {card}"
                )
                continue

            try:
                events = self._fetch_show_events(show_id)
            except Exception as error:
                self.custom_logger.warning(
                    f"Events fetch failed for '{title}' ({show_id}): {error}"
                )
                continue

            future_events = [
                event
                for event in events
                if event.get("attributes", {}).get("day")
                and event["attributes"]["day"] >= today_iso
            ]

            venue_info: dict[str, Any] = {}
            performances: list[dict[str, str]] = []
            seat_pricing: dict[str, list[dict[str, Any]]] = {}
            show_capacity = 0

            if future_events:
                for event in future_events:
                    parsed_event = self._parse_event(event)
                    if not parsed_event:
                        continue

                    if not venue_info and parsed_event.get("venue"):
                        venue_info = parsed_event["venue"]

                    datetime_key = format_datetime_key(
                        parsed_event["date"], parsed_event["time"]
                    )
                    if datetime_key:
                        performances.append(
                            {"date": parsed_event["date"], "time": parsed_event["time"]}
                        )
                        seat_pricing[datetime_key] = parsed_event["seat_pricing"]
                        if (
                            parsed_event["capacity"]
                            and parsed_event["capacity"] > show_capacity
                        ):
                            show_capacity = parsed_event["capacity"]
            else:
                self.custom_logger.info(
                    f"No future events for '{title}' — extracting with empty performances"
                )
                for event in events:
                    parsed_event = self._parse_event(event)
                    if parsed_event and parsed_event.get("venue"):
                        venue_info = parsed_event["venue"]
                        break

            rows.append(
                {
                    "title": title,
                    "venue_url": card.get("venue_url", ""),
                    "category": card.get("category"),
                    "venue": venue_info.get("name", VENUE_DEFAULTS["name"]),
                    "address": venue_info.get("address", VENUE_DEFAULTS["address"]),
                    "city": venue_info.get("city", VENUE_DEFAULTS["city"]),
                    "country": venue_info.get("country", VENUE_DEFAULTS["country"]),
                    "open_date": card.get("open_date"),
                    "close_date": card.get("close_date"),
                    "booking_start_date": card.get("open_date"),
                    "booking_end_date": card.get("close_date"),
                    "upcoming_performances": performances,
                    "capacity": show_capacity if show_capacity else None,
                    "currency": CURRENCY_CODE,
                    "is_limited_run": None,
                    "seat_pricing": seat_pricing,
                    "scrape_datetime": get_scrape_datetime(),
                }
            )
            self.custom_logger.info(
                f"Added: {title} — {len(performances)} perf(s), "
                f"seat_keys={len(seat_pricing)}, capacity={show_capacity}"
            )

            human_delay(*DELAY_BETWEEN_CALLS)

        return pd.DataFrame(rows)

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Coerce capacity to nullable integer."""
        if "capacity" in df.columns:
            df["capacity"] = pd.to_numeric(df["capacity"], errors="coerce").astype(
                "Int64"
            )
        return df

    def _transform_to_uniform_schema(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map Venue Cymru rows to the canonical Event schema.

        Delegates to BaseExtractor._transform_to_uniform_schema for the shared
        filtering/dedup/Event-construction logic. The one Venue Cymru-specific
        behavior — a single-performance show's open_date should prefer the
        performance date over the listing's open_date field — is applied by
        pre-adjusting ``open_date`` on a copy of the row before delegating,
        rather than reimplementing the whole method.
        """
        df = df.copy()
        for index, row in df.iterrows():
            performances = row.get("upcoming_performances") or []
            performance_dates = [
                performance["date"]
                for performance in performances
                if isinstance(performance, dict) and performance.get("date")
            ]
            if len(performance_dates) == 1:
                df.at[index, "open_date"] = performance_dates[0]

        return super()._transform_to_uniform_schema(df)

    def _establish_session(self) -> None:
        """Visit Ticketbooth entry page so the session cookie is set."""
        try:
            response = self.session.get(
                f"{TICKETSOLVE_BASE}/ticketbooth/shows", timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            self.custom_logger.info(
                f"Ticketsolve session established ({len(self.session.cookies)} cookie(s))"
            )
        except Exception as error:
            self.custom_logger.warning(f"Session establishment warning: {error}")

    def _scrape_all_cards(self) -> list[dict[str, Any]]:
        """Walk every pagination page and collect show cards."""
        cards: list[dict[str, Any]] = []
        page_url = WHATS_ON_URL
        seen_show_ids: set[str] = set()
        seen_page_urls: set[str] = set()

        while page_url and len(seen_page_urls) < MAX_PAGES:
            if page_url in seen_page_urls:
                self.custom_logger.warning(
                    f"Pagination loop detected at {page_url}, stopping"
                )
                break
            seen_page_urls.add(page_url)

            try:
                response = self.session.get(page_url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
            except Exception as error:
                self.custom_logger.warning(
                    f"Listing page fetch failed ({page_url}): {error}"
                )
                break

            soup = BeautifulSoup(response.text, "html.parser")
            page_cards = self._parse_listing_page(soup)
            for card in page_cards:
                show_id = card.get("show_id")
                if show_id and show_id not in seen_show_ids:
                    seen_show_ids.add(show_id)
                    cards.append(card)

            page_url = self._next_page_url(soup, page_url)
            if page_url:
                human_delay(*DELAY_BETWEEN_CALLS)

        return cards

    def _parse_listing_page(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Extract show cards from one listing page, filtering to allowed genres."""
        cards: list[dict[str, Any]] = []
        for card in soup.find_all("li", class_="matchheight"):
            title_link = card.select_one("h3 a")
            book_link = card.select_one("a.btn.book")
            if not title_link or not book_link:
                continue

            href = title_link.get("href", "")
            venue_url = href if href.startswith("http") else f"{BASE_URL}{href}"

            book_href = book_link.get("href", "")
            match = _SHOW_ID_RE.search(book_href)
            if not match:
                continue
            show_id = match.group(1)

            genres = {
                anchor.get_text(strip=True)
                for anchor in card.select("ul.event-genre a")
            }
            matched_genres = genres & ALLOWED_GENRES
            if not matched_genres:
                continue

            category = None
            for genre in sorted(matched_genres):
                category = standardize_category(genre) or GENRE_CATEGORY_MAP.get(genre)
                if category:
                    break

            time_tags = card.select(".dates time")
            datetimes = [
                tag.get("datetime", "")[:10] for tag in time_tags if tag.get("datetime")
            ]
            open_date = datetimes[0] if datetimes else None
            close_date = datetimes[-1] if datetimes else open_date

            cards.append(
                {
                    "title": title_link.get_text(strip=True),
                    "venue_url": venue_url,
                    "show_id": show_id,
                    "open_date": open_date,
                    "close_date": close_date,
                    "book_url": book_href,
                    "genres": sorted(genres),
                    "category": category,
                }
            )
        return cards

    def _next_page_url(self, soup: BeautifulSoup, current_url: str) -> str | None:
        """Return the next listing page URL, or None if there is not one."""
        pager = soup.find("nav", class_="pager")
        if not pager:
            return None

        current_page = 0
        if "page=" in current_url:
            try:
                current_page = int(re.search(r"page=(\d+)", current_url).group(1))
            except Exception:
                pass

        next_link = pager.find(
            "a", title=lambda title: title and "next page" in title.lower()
        )
        if next_link:
            href = next_link.get("href", "")
            return self._resolve_pagination_url(href)

        next_page = current_page + 1
        for link in pager.find_all("a", href=True):
            href = link["href"]
            if "/cy/" in href:
                continue
            if f"page={next_page}" in href:
                return self._resolve_pagination_url(href)
        return None

    def _resolve_pagination_url(self, href: str) -> str:
        """Build an absolute URL from a Drupal pager href."""
        if href.startswith("?"):
            return f"{WHATS_ON_URL}{href}"
        if href.startswith("/"):
            return f"{BASE_URL}{href}"
        return href if href.startswith("http") else f"{WHATS_ON_URL}{href}"

    def _fetch_show_events(self, show_id: str) -> list[dict[str, Any]]:
        """Fetch all events for a show with retries on transient errors."""
        url = (
            f"{API_BASE}/events"
            f"?filter%5Bshow%5D={show_id}"
            f"&page%5Blimit%5D=100"
            f"&include={EVENT_INCLUDE}"
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                data = response.json()

                included = {
                    (included["type"], included["id"]): included
                    for included in data.get("included", [])
                }
                for event in data.get("data", []):
                    event["__included"] = included
                return data.get("data", [])
            except Exception as error:
                last_error = error
                self.custom_logger.warning(
                    f"Events fetch attempt {attempt + 1}/3 failed for {show_id}: {error}"
                )
                if attempt < 2:
                    human_delay(1, 3)
        raise last_error or RuntimeError(f"Failed to fetch events for {show_id}")

    def _parse_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Return performance date/time, venue info, capacity and seat pricing."""
        attributes = event.get("attributes", {})
        relationships = event.get("relationships", {})
        included = event.get("__included", {})

        day = attributes.get("day")
        time_raw = attributes.get("time-of-day")
        if not day or not time_raw:
            return None

        try:
            performance_time = dateutil_parser.parse(time_raw).strftime("%H:%M")
        except Exception:
            return None

        venue_info = self._extract_venue_info(relationships, included)
        total_capacity, all_seats, grid_fetch_failed = self._extract_capacity_and_seats(
            relationships, included
        )

        return {
            "date": day,
            "time": performance_time,
            "venue": venue_info,
            "capacity": total_capacity,
            # None (not []) when a seated allocation's grid fetch failed, so a
            # failed API call isn't indistinguishable from a genuinely sold-out
            # or unreserved-seating performance.
            "seat_pricing": None if grid_fetch_failed else all_seats,
        }

    def _extract_venue_info(
        self, relationships: dict[str, Any], included: dict[tuple[str, str], Any]
    ) -> dict[str, Any] | None:
        """Return venue name/address/city/country from an event's included venue resource."""
        venue_id = (relationships.get("venue", {}).get("data") or {}).get("id")
        if not venue_id:
            return None

        venue_attributes = included.get(("venues", venue_id), {}).get("attributes", {})
        address_parts = [
            venue_attributes.get("address-1"),
            venue_attributes.get("address-2"),
            venue_attributes.get("address-3"),
        ]
        address = (
            ", ".join(part for part in address_parts if part)
            or VENUE_DEFAULTS["address"]
        )
        postcode = venue_attributes.get("post-code") or ""
        if postcode and postcode not in address:
            address = f"{address}, {postcode}".strip(", ")
        raw_country = venue_attributes.get("country") or VENUE_DEFAULTS["country"]
        return {
            "name": venue_attributes.get("name") or VENUE_DEFAULTS["name"],
            "address": address,
            "city": venue_attributes.get("city-town") or VENUE_DEFAULTS["city"],
            "country": normalize_country(raw_country) or VENUE_DEFAULTS["country"],
        }

    def _extract_capacity_and_seats(
        self, relationships: dict[str, Any], included: dict[tuple[str, str], Any]
    ) -> tuple[int, list[dict[str, Any]], bool]:
        """Aggregate total capacity and available seat/price pairs across master allocations.

        Returns (total_capacity, all_seats, grid_fetch_failed). grid_fetch_failed
        is True if any seated allocation's grid could not be fetched, so the
        caller can tell a failed API call apart from a genuinely full/unreserved
        allocation rather than silently substituting the coarser allocation_size.
        """
        allocation_references = relationships.get("master-allocations", {}).get(
            "data", []
        )
        total_capacity = 0
        all_seats: list[dict[str, Any]] = []
        grid_fetch_failed = False

        for reference in allocation_references:
            allocation = included.get(("master-allocations", reference["id"]))
            if not allocation:
                continue

            allocation_attributes = allocation.get("attributes", {})
            allocation_size = allocation_attributes.get("size") or 0

            if not allocation_attributes.get("seated"):
                total_capacity += int(allocation_size)
                continue

            grid = self._fetch_grid(reference["id"])
            if grid:
                non_blocked_count = 0
                for seat in grid.get("included", []):
                    if seat.get("type") != "seat-assignments":
                        continue
                    seat_assignment = seat.get("attributes", {})
                    if self._is_blocked_seat(seat_assignment):
                        continue
                    non_blocked_count += 1
                total_capacity += non_blocked_count
            else:
                grid_fetch_failed = True
                total_capacity += int(allocation_size)

            sold_out = allocation_attributes.get("soldout", False)
            available_count = allocation_attributes.get("available", 0) or 0
            if sold_out or available_count == 0:
                continue

            prices = self._collect_allocation_prices(allocation, included)
            if not prices or not grid:
                continue
            zone_price = max(prices)

            for seat in grid.get("included", []):
                if seat.get("type") != "seat-assignments":
                    continue
                seat_assignment = seat.get("attributes", {})
                if seat_assignment.get(
                    "status"
                ) != "available" or self._is_blocked_seat(seat_assignment):
                    continue
                # Avoid literal apostrophes, which would break ast.literal_eval when
                # the seat_pricing cell is re-parsed downstream (docs/csv-validator.md rule 17).
                section = (
                    str(seat_assignment.get("section", "")).strip().replace("'", "’")
                )
                row = str(seat_assignment.get("row", "")).strip().replace("'", "’")
                number = (
                    str(seat_assignment.get("number", "")).strip().replace("'", "’")
                )
                seat_label = f"{section} - {row} {number}".strip(" -")
                all_seats.append({"seat": seat_label, "ticket_price": zone_price})

        return total_capacity, all_seats, grid_fetch_failed

    def _collect_allocation_prices(
        self, allocation: dict[str, Any], included: dict[tuple[str, str], Any]
    ) -> list[float]:
        """Return all ticket prices for a master allocation's ticket-allocations."""
        prices: list[float] = []
        for ticket_allocation_reference in (
            allocation.get("relationships", {})
            .get("ticket-allocations", {})
            .get("data", [])
        ):
            ticket_allocation = included.get(
                ("ticket-allocations", ticket_allocation_reference["id"])
            )
            if not ticket_allocation:
                continue
            for price_reference in (
                ticket_allocation.get("relationships", {})
                .get("event-ticket-prices", {})
                .get("data", [])
            ):
                price_item = included.get(
                    ("event-ticket-prices", price_reference["id"])
                )
                if not price_item:
                    continue
                price = price_item.get("attributes", {}).get("price")
                if price is not None:
                    try:
                        prices.append(float(price))
                    except (TypeError, ValueError):
                        pass
        return prices

    def _is_blocked_seat(self, seat_assignment: dict[str, Any]) -> bool:
        """Return True if a seat-assignment represents a blocked/non-existent seat."""
        return (
            bool(seat_assignment.get("blocked"))
            or seat_assignment.get("status") == "blocked"
        )

    def _fetch_grid(self, allocation_id: str) -> dict[str, Any] | None:
        """Fetch the seat grid for a master allocation, with retries."""
        url = f"{API_BASE}/grids/{allocation_id}?include=seat-assignments"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                human_delay(*DELAY_BETWEEN_CALLS)
                return response.json()
            except Exception as error:
                last_error = error
                self.custom_logger.warning(
                    f"Grid fetch attempt {attempt + 1}/3 failed ({allocation_id}): {error}"
                )
                if attempt < 2:
                    human_delay(1, 3)
        self.custom_logger.error(f"Grid fetch failed ({allocation_id}): {last_error}")
        return None


def main():
    extractor = VenueCymruExtractor(
        save_csv_locally=False,
        csv_incremental_mode=False,
    )
    result = extractor.run()
    logger.info(f"Extraction result: {result}")
    if result.get("status") not in ("success", "validation_failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
