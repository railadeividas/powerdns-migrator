from dataclasses import dataclass


@dataclass
class PowerDNSConnection:
    """Connection configuration for a PowerDNS API endpoint."""

    base_url: str
    api_key: str
    server_id: str = "localhost"
    verify_ssl: bool = True

    def endpoint(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/api/v1/servers/{self.server_id}{path}"
