# api/routers/__init__.py
"""
FastAPI routers. Each module in this package exports a single APIRouter
instance, which api/main.py mounts via app.include_router(...).

Phase 2 ships:    auth.py       (login, refresh, logout, register, me)
Phase 4 will add: admin_users.py
                  admin_invitations.py
                  admin_audit.py
                  admin_system.py
                  admin_settings.py
"""