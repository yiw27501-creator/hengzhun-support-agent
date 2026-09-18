"""Read-only business tool boundary. No credentials, SQL or authority from a model."""
import json
import time
import uuid
from typing import Protocol
from engine import now

NAMES = ('get_order', 'check_inventory', 'get_purchase_relation', 'get_product', 'get_execution_receipt')


def tool_definitions():
    """Portable function schemas; not evidence of a provider or MCP connection."""
    return [dict(name=name, description='Read current task only: '+name,
                 parameters=dict(type='object', properties={}, additionalProperties=False))
            for name in NAMES]


class ReadAdapter(Protocol):
    def read(self, connection, name: str, order_id: str) -> dict: ...


class UnconfiguredAdapter:
    def read(self, connection, name, order_id):
        raise ConnectionError('Business adapter is not configured')


class SandboxAdapter:
    """SQLite order/receipt reads plus explicit synthetic department fixtures."""
    def read(self, c, name, order_id):
        row = c.execute('SELECT facts FROM orders WHERE id=?', (order_id,)).fetchone()
        if row is None: raise LookupError('Order unavailable')
        facts = json.loads(row[0])
        if name == 'get_order':
            return dict(provider='sandbox-orders', order_id=order_id,
                        **{k: facts.get(k) for k in ('payment', 'paid_minor', 'return_required')})
        if name == 'check_inventory':
            return dict(provider='simulated-inventory', sku='ALT-A', stock=3,
                        price_minor=18900, checked_at=now())
        if name == 'get_purchase_relation':
            return dict(provider='simulated-purchase', gift_order=True, authorization_granted=False)
        if name == 'get_product':
            return dict(provider='simulated-catalog', sku='ALT-A', price_minor=18900)
        row = c.execute('SELECT receipt FROM executions WHERE order_id=?', (order_id,)).fetchone()
        return dict(provider='sandbox-receipts', status='found' if row else 'not_found',
                    receipt=json.loads(row[0]) if row else None)


class ToolGateway:
    def __init__(self, engine, adapter=None):
        self.engine = engine
        self.adapter = adapter if adapter is not None else SandboxAdapter()

    def call(self, c, task_id, name, arguments):
        # task_id comes from the authenticated server workflow, never model arguments.
        if name not in NAMES: raise ValueError('Tool not allowed')
        if type(arguments) is not dict or arguments: raise ValueError('Unexpected tool arguments')
        row = c.execute('SELECT order_id FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None: raise KeyError(task_id)
        call_id = uuid.uuid4().hex
        start = time.monotonic()
        try:
            data = self.adapter.read(c, name, row[0])
            if type(data) is not dict: raise ValueError('Invalid adapter output')
            if name in {'check_inventory', 'get_product'}:
                if not isinstance(data.get('sku'), str) or not 1 <= len(data['sku']) <= 80:
                    raise ValueError('Invalid SKU')
                if type(data.get('price_minor')) is not int or data['price_minor'] < 0:
                    raise ValueError('Invalid price')
            if name == 'check_inventory':
                if type(data.get('stock')) is not int or data['stock'] < 0 or not isinstance(data.get('checked_at'), str):
                    raise ValueError('Invalid inventory')
            if name == 'get_purchase_relation':
                if type(data.get('gift_order')) is not bool:
                    raise ValueError('Invalid purchase relation')
                data = dict(data, authorization_granted=False)
            result = dict(ok=True, data=data, error=None)
        except Exception as exc:
            # Never leak credentials, URLs, SQL, or provider response bodies.
            code = 'timeout' if isinstance(exc, TimeoutError) else 'adapter_unavailable'
            result = dict(ok=False, data=None, error=code)
        result.update(call_id=call_id, tool=name, schema_version=1,
                      sandbox=isinstance(self.adapter, SandboxAdapter),
                      latency_ms=round((time.monotonic()-start)*1000))
        self.engine.trace(c, task_id, 'tool.called', **{k: result[k] for k in
                          ('call_id', 'tool', 'schema_version', 'sandbox', 'latency_ms', 'ok', 'error')})
        return result
