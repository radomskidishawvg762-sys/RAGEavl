from __future__ import annotations

from typing import TypeVar

from sqlalchemy.orm import Session

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository[ModelT: Base]:
    """Sole data-access boundary.

    Services depend on repositories and must never touch Session/ORM directly (P-8).
    Concrete repositories are added in later milestones; this base holds the session
    and model reference common to all of them.
    """

    def __init__(self, session: Session, model: type[ModelT]) -> None:
        self._session = session
        self._model = model

    @property
    def session(self) -> Session:
        return self._session
