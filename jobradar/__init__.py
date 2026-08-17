"""job-radar: watch employer job boards directly.

Pulls postings straight from company applicant tracking systems rather than
through a job aggregator, normalises eleven different API shapes into one
schema, and filters them against rules you write down once.
"""

__version__ = "0.1.0"

from .models import Job, Salary, Source  # noqa: F401

__all__ = ["Job", "Salary", "Source", "__version__"]
