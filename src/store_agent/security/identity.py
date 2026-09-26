from typing import Protocol

from store_agent.config import Settings
from store_agent.contracts import UserContext


class IdentityProvider(Protocol):
    def resolve(self, user_id: str) -> UserContext | None:
        """Map an authenticated principal (Entra object id in production) to user context.
        Returns None for unknown principals; callers must fail closed."""
        ...


class DevIdentityProvider:
    """Reads config/dev_users.yaml. Stand-in for Entra + HR directory lookup."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def resolve(self, user_id: str) -> UserContext | None:
        u = self.settings.dev_users.get(user_id)
        if u is None:
            return None
        store = self.settings.catalog.stores.get(u.store_id)
        role = self.settings.capabilities.roles.get(u.role)
        return UserContext(
            user_id=user_id,
            display_name=u.display_name,
            store_id=u.store_id,
            role=u.role,
            market_id=u.market_id or (store.market_id if store else None),
            timezone=u.timezone or (store.timezone if store else "UTC"),
            permissions=[str(c) for c in role.capabilities] if role else [],
        )
