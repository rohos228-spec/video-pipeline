import importlib.util
from pathlib import Path
from types import SimpleNamespace

path = Path('/test-source/gateway.py')
spec = importlib.util.spec_from_file_location('gateway_under_test', path)
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
gateway.FIXTURE = False
gateway.EXTERNAL_CALLS_ALLOWED = True

for origin, method, request_path in [
    ('http://api.kie.ai', 'POST', '/codex/v1/responses'),
    ('https://foreign.invalid', 'POST', '/codex/v1/responses'),
    ('https://api.kie.ai:8443', 'POST', '/codex/v1/responses'),
    ('https://api.kie.ai/other', 'POST', '/codex/v1/responses'),
    ('https://api.kie.ai', 'GET', '/codex/v1/responses'),
    ('https://api.kie.ai', 'POST', '/other'),
]:
    gateway.KIE = origin
    denied = []
    fake = SimpleNamespace(command=method, path=request_path,
                           reject=lambda code, reason: denied.append((code, reason)))
    gateway.Handler.forward(fake, b'{}')
    assert denied == [(403, 'external_route_blocked')], (origin, method, request_path, denied)

gateway.KIE = 'https://api.kie.ai'
gateway.EXTERNAL_CALLS_ALLOWED = False
denied = []
fake = SimpleNamespace(command='POST', path='/codex/v1/responses',
                       reject=lambda code, reason: denied.append((code, reason)))
gateway.Handler.forward(fake, b'{}')
assert denied == [(403, 'external_gateway_disabled')]
print('gateway route denials: 7 passed')
