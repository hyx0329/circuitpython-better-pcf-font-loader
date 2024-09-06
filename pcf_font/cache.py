from collections import OrderedDict

try:
    from typing import Union, Optional, Any
except ImportError:
    pass

class LruCache:
    def __init__(self, capacity: int) -> None:
        self._cache = OrderedDict()
        self._capacity = capacity

    def get(self, key: Union[int, str]) -> Optional[Any]:
        data = self._cache.get(key, None)
        if data is not None:
            # move the key to head/front
            # for popitem in CircuitPython only support LIFO order
            self._cache.move_to_end(key, last=False)
        return data

    def put(self, key: Union[int, str], value: Any) -> None:
        if len(self._cache) >= self._capacity:
            # remove the last item
            # TODO: discard in batch, like 10% once
            self._cache.popitem()
        self._cache[key] = value
        self._cache.move_to_end(key, last=False)
    
    def contains(self, key: Union[int, str]) -> bool:
        return key in self._cache
