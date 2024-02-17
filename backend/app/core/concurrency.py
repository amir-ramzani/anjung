from collections.abc import Callable

from app.core.errors import Conflict
from app.repositories.dynamo import TxCancelled


def retry_on_version_conflict[T](
    fn: Callable[[], T], attempts: int = 3, index: int | tuple[int, ...] = 0
) -> T:
    """Run a read-modify-write. If a version-checked operation (at `index` in the
    transaction) fails, the whole function re-reads and retries."""
    indexes = (index,) if isinstance(index, int) else index
    for attempt in range(attempts):
        try:
            return fn()
        except TxCancelled as e:
            if not (e.conflicted or any(e.failed(i) for i in indexes)):
                raise
            if attempt == attempts - 1:
                raise Conflict("The resource was modified by someone else, retry") from None
    raise AssertionError("unreachable")  # pragma: no cover
