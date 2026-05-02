# core/__init__.py
"""
Core cross-cutting primitives — security, configuration, and other utilities
that are not tied to a specific domain (auth, ML, ingestion).

Modules in this package MUST NOT import from `api/`, `domain/`, `services/`,
or `repository/` to avoid circular dependencies. Anything in `core/` is a
*leaf* dependency — it can be imported by anyone, but imports nothing of ours.
"""