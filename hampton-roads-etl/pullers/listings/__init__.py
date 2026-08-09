"""Rent listings scraper — Eight Rock's in-house alternative to HelloData.

Pulls live asking rents + concessions from public listing aggregators
(Apartments.com initially; Zillow Rentals + Rent.com / RentCafe to follow
in week 2). Writes to a single ``rent_listings`` table in
``hampton_roads.db``.

Architecture::

    pullers/listings/
    ├── __init__.py           # exports `pull_listings`
    ├── base.py               # BaseListingScraper protocol + polite HTTP
    ├── concessions.py        # ConcessionParser + effective-rent math
    ├── apartments_com.py     # ApartmentsDotComScraper
    └── runner.py             # main entry point — iterates ALN universe

Each source is a subclass of ``BaseListingScraper`` so adding a new source
is a one-file add to ``pullers/listings/`` plus a registry line in
``runner.py``. The runner handles polite throttling, retry budget,
status tracking, and per-property URL caching (subsequent runs skip the
search step when the listing URL is already known).
"""

from .runner import pull_listings

__all__ = ["pull_listings"]
