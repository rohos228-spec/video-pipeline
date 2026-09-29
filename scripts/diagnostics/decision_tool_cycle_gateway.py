"""Bind the existing diagnostic gateway to the authorized Decision pilot."""
import importlib.util
import json
import os
from pathlib import Path

SOURCE = Path('/gateway-source')
CONFIG = json.loads((SOURCE / 'pilot-config.json').read_text())
assert CONFIG == {
    'version': 1, 'model': 'gpt-5-6-sol', 'endpoint': 'https://api.kie.ai/codex/v1/responses',
    'max_requests': 3, 'max_request_bytes': 30000, 'max_output_tokens': 1024,
    'allowed_actions': ['read_events', 'read_contract', 'wait', 'human_review'],
    'business_writes': False, 'automatic_retries': 0,
}
spec = importlib.util.spec_from_file_location('existing_gateway', SOURCE / 'gateway.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.MAX_REQUESTS = CONFIG['max_requests']
base.MAX_BYTES = CONFIG['max_request_bytes']
base.MAX_OUTPUT = CONFIG['max_output_tokens']
base.EXTERNAL_CALLS_ALLOWED = False
base.LEDGER = Path('/results/decision-gateway.sqlite')
base.SOCKET = Path('/gateway/llm.sock')

class Handler(base.Handler):
    def do_GET(self):
        self.reject(403, 'decision_retrieval_forbidden')

    def do_POST(self):
        if self.path != '/codex/v1/responses':
            self.reject(403, 'route_blocked')
            return
        super().do_POST()

    def forward(self, body):
        # Any implicit continuation or salvage POST lacks the Decision context.
        # It must fail before the gateway reserves/sends a paid request.
        if self.command != 'POST' or not all(part in body for part in
                (b'bounded diagnostic supervisor', b'context_version', b'allowed_actions')):
            self.reject(403, 'non_decision_post_forbidden')
            return
        super().forward(body)

if __name__ == '__main__':
    from gateway_egress_probe import run as verify_network
    verify_network(os.environ['PINNED_KIE_IPV4'])
    base.EXTERNAL_CALLS_ALLOWED = True  # After local policy and process probes, before serving.
    base.SOCKET.parent.mkdir(parents=True, exist_ok=True)
    if base.SOCKET.exists():
        raise SystemExit('Socket already exists; refusing to replace it')
    base.recover_pending()
    with base.Server(str(base.SOCKET), Handler) as server:
        os.chmod(base.SOCKET, 0o666)
        print('gateway_ready', flush=True)
        server.serve_forever()
