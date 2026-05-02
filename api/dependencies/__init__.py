# api/dependencies/__init__.py
"""
FastAPI dependency callables. Each module in this package exports a set
of `Depends`-able functions; routers compose them.

Why a package, not a single file:
  Phase 2 ships auth dependencies. Phase 4+ may add request-scoped
  dependencies for rate limiting, feature flags, or per-tenant context.
  Keeping them in subject-named modules under api/dependencies/ keeps
  the import surface clean.
"""