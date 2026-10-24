"""Base repository — generic CRUD patterns every repository inherits.

Eliminates boilerplate: subclasses declare their model type and get
standard create/get/list/delete for free. Only domain-specific queries
need to be written in the subclass.
"""

from __future__ import annotations

import logging
from typing import Any, Generic, TypeVar

from sqlmodel import Session, SQLModel, col, select

T = TypeVar("T", bound=SQLModel)

logger = logging.getLogger(__name__)


class BaseRepository(Generic[T]):
    """Generic repository with standard CRUD operations.

    Subclasses set ``model`` to their SQLModel table class and
    inherit create, get_by_id, list_all, and delete_by_id.
    """

    model: type[T]

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, entity: T) -> T:
        """Insert a new entity and flush to get the ID."""
        self._session.add(entity)
        self._session.flush()
        return entity

    def get_by_id(self, entity_id: Any) -> T | None:
        """Fetch a single entity by primary key."""
        return self._session.get(self.model, entity_id)

    def list_all(self, limit: int = 100) -> list[T]:
        """Fetch all rows, capped by limit."""
        stmt = select(self.model).limit(limit)
        return list(self._session.exec(stmt).all())

    def delete_by_id(self, entity_id: Any) -> bool:
        """Delete an entity by primary key. Returns True if it existed."""
        entity = self.get_by_id(entity_id)
        if entity:
            self._session.delete(entity)
            self._session.flush()
            return True
        return False

    def count(self) -> int:
        """Total rows in the table."""
        return len(self._session.exec(select(self.model)).all())

    def exists(self, entity_id: Any) -> bool:
        """Check if an entity exists without loading it fully."""
        return self.get_by_id(entity_id) is not None
