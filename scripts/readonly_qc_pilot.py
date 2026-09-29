"""Standalone diagnostic pilot; never calls the Decision/Actions executor."""

async def evaluate_once(context, results, execution_id, *, synthetic_transport=None):
    raise NotImplementedError('one-shot adapter not implemented')
