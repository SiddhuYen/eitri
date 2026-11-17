"""What eitri's own HTTPS requests share (the update check, the anonymous counts)."""
from __future__ import annotations


def tls_context():
    """certifi's CA bundle when it is installed (it comes with anthropic/httpx), else the system default. Some macOS Pythons
    (uv's own builds, python.org's framework Python without its "Install Certificates" step) have no CA file, so the default
    context fails every HTTPS request with CERTIFICATE_VERIFY_FAILED."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - no certifi, or its bundle is unreadable
        return ssl.create_default_context()
