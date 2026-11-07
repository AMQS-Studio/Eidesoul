"""The Agent's delivery facade: every outbound reply goes through one `Delivery`."""

from pathlib import Path
from typing import TYPE_CHECKING

from .inbound import Address, GatewayCore, Percept

if TYPE_CHECKING:
    from .turn.delivery import Delivery

class DeliveryFacade:
    """Thin, typed forwarding to the injected `Delivery`; no policy lives here."""

    delivery: Delivery

    async def deliver_file(
        self,
        thread_id: str,
        path: Path,
        caption: str = "",
        *,
        gateway: GatewayCore,
        reply: Percept | None = None,
        origin: Address | None = None,
        reason: str = "",
    ) -> str:
        return await self.delivery.file(
            thread_id,
            path,
            caption,
            gateway=gateway,
            reply=reply,
            origin=origin,
            reason=reason,
        )

    async def send_text(
        self,
        thread_id: str,
        text: str,
        *,
        gateway: GatewayCore | None,
        subject: str = "",
        origin: Address | None = None,
        reason: str = "",
    ) -> str:
        return await self.delivery.send_text(
            thread_id, text, gateway=gateway, subject=subject, origin=origin, reason=reason
        )

    async def send_file(
        self,
        thread_id: str,
        path: Path,
        caption: str = "",
        *,
        gateway: GatewayCore | None,
        subject: str = "",
        origin: Address | None = None,
        reason: str = "",
        action_id: str = "",
    ) -> str:
        return await self.delivery.send_file(
            thread_id,
            path,
            caption,
            gateway=gateway,
            subject=subject,
            origin=origin,
            reason=reason,
            action_id=action_id,
        )
