"""Adapter for the Colombian public/private IPS catalog through SODA3."""

from .client import DATASET_ID, SODA3_URL, Soda3Client, Soda3Error
from .service import IPSService

__all__ = [
    "DATASET_ID",
    "IPSService",
    "SODA3_URL",
    "Soda3Client",
    "Soda3Error",
]
