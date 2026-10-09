"""Reproducible Golden benchmark for the stateful IPS agent.

The package deliberately has no import side effects: importing it does not
connect to PostgreSQL, Chroma, an LLM provider, or JEV.  Use the CLI or the
small public helpers from :mod:`snapshot` and :mod:`deterministic` when an
explicit evaluation is desired.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
