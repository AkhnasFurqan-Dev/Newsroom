from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import qrcode
from qrcode.constants import ERROR_CORRECT_M

from ..security import validate_public_http_url


@dataclass
class QRResult:
    path: Path | None
    target: str
    reason: str
    verified: bool


def verify_reachable(url: str, timeout: float = 6.0) -> tuple[bool, str]:
    ok, reason = validate_public_http_url(url)
    if not ok:
        return False, reason
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": "ChannelPress/1.0"}) as client:
            response = client.get(url)
        if 200 <= response.status_code < 400:
            return True, f"HTTP {response.status_code}"
        return False, f"HTTP {response.status_code}"
    except Exception as exc:
        return False, f"Unreachable: {type(exc).__name__}"


def make_qr(
    target: str,
    output: Path,
    *,
    require_reachable: bool = True,
    public_base_url: str = "",
) -> QRResult:
    """Create a print QR only for a verifiably reachable public URL.

    Localhost, private-network and placeholder URLs are rejected. When the
    target cannot be verified, the caller must fall back to the printed source
    appendix instead of printing a misleading code.
    """
    target = target.strip()
    if not target:
        return QRResult(None, target, "No QR target configured; source appendix used.", False)
    if "localhost" in target or "127.0.0.1" in target or target.startswith("file:"):
        return QRResult(None, target, "Local/offline targets are never encoded in print.", False)
    if public_base_url and not target.startswith(public_base_url.rstrip("/")):
        return QRResult(None, target, "QR target is outside the configured public publication origin.", False)
    verified, detail = verify_reachable(target) if require_reachable else validate_public_http_url(target)
    if not verified:
        return QRResult(None, target, f"QR target not verified ({detail}); source appendix used.", False)
    output.parent.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=12, border=3)
    qr.add_data(target)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#181716", back_color="#f4f1e9")
    image.save(output)
    return QRResult(output, target, f"Verified ({detail}).", True)


def text_qr_payload(sources: list[str], max_chars: int = 110) -> str | None:
    payload = "\n".join(sources)
    if not payload or len(payload) > max_chars:
        return None
    if any("localhost" in url or url.startswith("file:") for url in sources):
        return None
    return payload
