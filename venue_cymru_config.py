"""Configuration for Venue Cymru scraper."""

BASE_URL = "https://www.venuecymru.co.uk"
WHATS_ON_URL = f"{BASE_URL}/whats-on"

TICKETSOLVE_BASE = "https://venuecymru.ticketsolve.com"
API_BASE = f"{TICKETSOLVE_BASE}/api/ticketbooth/v1"

REQUEST_TIMEOUT = 30
DELAY_BETWEEN_CALLS = (1.0, 2.5)

MAX_PAGES = 200

CURRENCY_CODE = "GBP"

VENUE_DEFAULTS = {
    "name": "Venue Cymru",
    "address": "The Promenade",
    "city": "Llandudno",
    "country": "United Kingdom",
    "postcode": "LL30 1BB",
}

EVENT_INCLUDE = (
    "venue"
    ",venue-layout.asset-attachment"
    ",master-allocations.ticket-zone"
    ",master-allocations.ticket-allocations.event-ticket-prices"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/vnd.api+json",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Listing genres to scrape. Anything outside these is skipped.
ALLOWED_GENRES = {"Family", "Musicals", "Theatre"}

# Map the listing genre to the canonical category.
# Family and Theatre are intentionally left as None; Musicals -> Musical.
GENRE_CATEGORY_MAP = {
    "Family": None,
    "Musicals": "Musical",
    "Theatre": None,
}
