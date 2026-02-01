"""Abstract interfaces for document delivery (SOLID - Interface Segregation, Dependency Inversion)."""

from abc import ABC, abstractmethod
from typing import Any


class IDocumentDeliverer(ABC):
    """Abstract interface for delivering a document to a recipient."""

    @abstractmethod
    async def deliver(
        self,
        document: bytes,
        filename: str,
        recipient: str,
        **kwargs: Any,
    ) -> bool:
        """
        Deliver document to recipient.

        :param document: Raw document bytes.
        :param filename: Original filename (e.g. doc.pdf).
        :param recipient: Email address or phone number, depending on implementation.
        :param kwargs: Implementation-specific options (subject, body, message, etc.).
        :return: True if delivery succeeded.
        """
        pass
