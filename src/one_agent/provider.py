"""Untrusted advisor boundary; no external service sees viewer data."""
import hashlib
from dataclasses import dataclass
from typing import Protocol
from .common import ts


@dataclass
class ProviderResult:
    output: object
    error: str | None = None
    simulated_wait_ms: float = 0


class Provider(Protocol):
    def propose(self, trigger, candidates, at) -> ProviderResult: ...


class ReplayProvider:
    def __init__(self, faults, controls, timeout_ms=2000):
        self.faults, self.controls, self.timeout_ms = faults, controls, timeout_ms

    def propose(self, trigger, candidates, at):
        fault = self.faults.get(trigger['event_id'])
        # Reproduce exact scripted outputs, even when eligibility later rejects them.
        if fault:
            if fault['fault'] == 'timeout':
                return ProviderResult(None, 'model_timeout', min(self.timeout_ms, fault['seconds'] * 1000))
            return ProviderResult(fault['model_output'])
        for c in self.controls:
            if c.get('dependency') == 'model_provider' and ts(c['at']) <= at < ts(c['until']):
                return ProviderResult(None, 'model_503')
        return ProviderResult({'goal_id': candidates[0] if candidates else None,
                               'reason': 'Use the first evidence-backed policy candidate.'})


def store_delay(trigger, controls, at, timeout_ms=1000):
    """Discrete-event latency injection, not a sleep or a measured production p95."""
    for c in controls:
        if c.get('dependency') == 'user_data_store' and ts(c['at']) <= at < ts(c['until']):
            bucket = int(hashlib.sha256(trigger['event_id'].encode()).hexdigest()[:8], 16) % 100
            if bucket < 20:
                return timeout_ms, 'store_timeout'
            if bucket >= 95:
                return min(8000, timeout_ms), 'store_slow_read_deadline' if timeout_ms < 8000 else None
            return 10, None
    return 0, None
