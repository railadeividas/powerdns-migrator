def normalize_zone_name(name: str) -> str:
    """Ensure a zone name ends with a trailing dot for API consistency."""
    return name if name.endswith(".") else f"{name}."
