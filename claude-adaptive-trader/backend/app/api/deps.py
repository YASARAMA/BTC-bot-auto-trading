"""Dependencies shared by the routers."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:  # pragma: no cover
    from app.services import Services


def services(request: Request) -> "Services":
    return request.app.state.services
