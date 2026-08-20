"""InfraDealer outbound integration — API client, events, outbox."""

from .service import InfraDealerIntegrationService, get_integration_service

__all__ = ["InfraDealerIntegrationService", "get_integration_service"]
