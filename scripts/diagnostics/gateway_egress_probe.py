"""Keyless probe in the actual gateway process, before its listener starts."""
import json
import os
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run(pinned_ip: str) -> dict:
    if not pinned_ip or socket.inet_pton(socket.AF_INET, pinned_ip) is None:
        raise RuntimeError('missing pinned IPv4')
    status = Path('/proc/self/status').read_text()
    caps = {line.split(':', 1)[0]: line.split(':', 1)[1].strip()
            for line in status.splitlines() if line.startswith(('CapEff:', 'CapBnd:'))}
    if caps != {'CapEff': '0000000000000000', 'CapBnd': '0000000000000000'}:
        raise RuntimeError('gateway process retained capabilities')
    denied_change = subprocess.run(
        ['nft', 'add', 'table', 'inet', 'gateway_forbidden_change'],
        capture_output=True, text=True, timeout=3)
    if denied_change.returncode == 0 or 'Operation not permitted' not in denied_change.stderr:
        raise RuntimeError('gateway can alter nft policy or denial unverified')
    addresses = {entry[4][0] for entry in socket.getaddrinfo('api.kie.ai', 443, type=socket.SOCK_STREAM)}
    if addresses != {pinned_ip}:
        raise RuntimeError('gateway DNS differs from pinned IPv4')
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 18772))
    listener.listen(2)
    try:
        direct = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        direct.settimeout(0.6)
        try:
            local_result = direct.connect_ex(('127.0.0.1', 18772))
        finally:
            direct.close()
        child = subprocess.run([sys.executable, '-c',
            "import socket; s=socket.socket(); s.settimeout(.6); print(s.connect_ex(('127.0.0.1',18772))); s.close()"],
            capture_output=True, text=True, timeout=3, check=True)
        child_result = int(child.stdout.strip())
        if local_result == 0 or child_result == 0:
            raise RuntimeError('gateway or child reached controlled listener')
    finally:
        listener.close()
    listener6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    listener6.bind(('::1', 18773))
    listener6.listen(1)
    try:
        direct6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        direct6.settimeout(0.6)
        try:
            ipv6_result = direct6.connect_ex(('::1', 18773))
        finally:
            direct6.close()
        if ipv6_result == 0:
            raise RuntimeError('IPv6 loopback bypassed default-deny policy')
    finally:
        listener6.close()
    dns_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dns_socket.settimeout(0.6)
    try:
        dns_result = dns_socket.connect_ex(('127.0.0.11', 53))
    finally:
        dns_socket.close()
    if dns_result == 0:
        raise RuntimeError('Docker DNS TCP remained reachable')
    with socket.create_connection((pinned_ip, 443), timeout=8) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname='api.kie.ai') as tls:
            if tls.version() is None:
                raise RuntimeError('TLS handshake incomplete')
            tls_version = tls.version()
    result = {'utc': datetime.now(timezone.utc).isoformat(), 'hostname': 'api.kie.ai',
              'pinned_ipv4': pinned_ip, 'capabilities': caps,
              'nft_change_denied': True, 'controlled_listener_active': True,
              'gateway_direct_connect_errno': local_result,
              'child_direct_connect_errno': child_result,
              'ipv6_direct_connect_errno': ipv6_result,
              'docker_dns_connect_errno': dns_result,
              'tls_hostname_verified': True, 'tls_version': tls_version,
              'http_requests': 0, 'bearer_present': False}
    Path('/results/gateway-network-probe.json').write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    print(json.dumps(run(os.environ['PINNED_KIE_IPV4'])), flush=True)
