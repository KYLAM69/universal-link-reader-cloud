"""Conservative public URL handling; a PoC, not an unrestricted URL proxy."""
from __future__ import annotations
import ipaddress
import os
import re
import socket
from urllib.parse import urlsplit

IG_HOSTS = frozenset({'www.instagram.com', 'instagram.com', 'm.instagram.com'})
META_MEDIA_DOMAINS = ('cdninstagram.com', 'fbcdn.net')
# Demo public MP4 origins. Exact host matches only. Can opt in to MORE hosts in Render env.
DEFAULT_MP4_HOSTS = ('interactive-examples.mdn.mozilla.net', 'media.w3.org')
POST_PATH = re.compile(r'^/(?:[A-Za-z0-9_.]{1,30}/)?(?:reel|p|tv)/([A-Za-z0-9_-]{5,32})/?$')


class RejectedURL(ValueError):
    pass


def _domain_or_subdomain(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith('.' + suffix)


def _parse_https(url: str):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 32 for c in url):
        raise RejectedURL('invalid_or_oversized_url')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)):
            raise RejectedURL('require_public_https_without_credentials_or_custom_port')
    except ValueError as exc:
        raise RejectedURL('invalid_https_url') from exc
    host = parsed.hostname.lower().rstrip('.')
    if host != parsed.hostname.lower():
        raise RejectedURL('noncanonical_hostname')
    if not re.fullmatch(r'[a-z0-9.-]{1,253}', host) or '..' in host:
        raise RejectedURL('invalid_hostname')
    try:
        ipaddress.ip_address(host)
        raise RejectedURL('ip_literal_not_allowed')
    except ValueError as exc:
        if isinstance(exc, RejectedURL):
            raise
    if parsed.fragment:
        raise RejectedURL('fragments_not_allowed')
    return parsed, host


def canonical_instagram(url: str) -> tuple[str, str]:
    parsed, host = _parse_https(url)
    if host not in IG_HOSTS or parsed.query:
        raise RejectedURL('unsupported_instagram_host_or_query')
    match = POST_PATH.fullmatch(parsed.path)
    if not match:
        raise RejectedURL('unsupported_instagram_post_path')
    shortcode = match.group(1)
    return f'https://www.instagram.com/reel/{shortcode}/', shortcode


def allowed_video_hosts() -> frozenset[str]:
    configured = os.environ.get('ULR_ALLOWED_MP4_HOSTS', '').strip()
    extra = tuple(x.lower().strip() for x in configured.split(',') if x.strip())
    return frozenset(DEFAULT_MP4_HOSTS + extra)


def validate_mp4_url(url: str) -> str:
    parsed, host = _parse_https(url)
    if host not in allowed_video_hosts():
        raise RejectedURL('mp4_host_not_allowlisted')
    if not parsed.path.lower().endswith('.mp4'):
        raise RejectedURL('require_direct_mp4_path')
    if parsed.query:
        raise RejectedURL('mp4_query_not_allowed_in_poc')
    return url


def validate_meta_cdn_url(url: str) -> str:
    _, host = _parse_https(url)
    if not any(_domain_or_subdomain(host, suffix) for suffix in META_MEDIA_DOMAINS):
        raise RejectedURL('not_allowed_meta_cdn')
    return url


def validate_public_dns(host: str) -> list[str]:
    """Reject any private/special-purpose resolved IP. No IP URLs or arbitrary hosts.

    TOCTOU/rebinding remains possible without a pinned-address transport. Hosting
    should additionally block egress to internal/private ranges at network level.
    """
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RejectedURL('dns_resolution_failed') from exc
    addresses = sorted(set(i[4][0].split('%')[0] for i in infos))
    if not addresses:
        raise RejectedURL('empty_dns_result')
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise RejectedURL('non_public_dns_address')
    return addresses
