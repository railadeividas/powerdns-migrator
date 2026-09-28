from dataclasses import dataclass


@dataclass
class PowerDNSConnection:
    """Connection configuration for a PowerDNS API endpoint.

    Attributes:
        base_url: API base URL, e.g. ``https://pdns:8081``.
        api_key: API key sent as the ``X-API-Key`` request header.
        server_id: PowerDNS server identifier (default: ``"localhost"``).
        verify_ssl: Whether to verify TLS certificates (default: ``True``).
    """

    base_url: str
    api_key: str
    server_id: str = "localhost"
    verify_ssl: bool = True

    def endpoint(self, path: str) -> str:
        """Build a full API URL for the given path.

        Args:
            path: API path relative to the server root, e.g. ``/zones``.

        Returns:
            Full URL in the form ``{base_url}/api/v1/servers/{server_id}{path}``.
        """
        base = self.base_url.rstrip("/")
        return f"{base}/api/v1/servers/{self.server_id}{path}"
