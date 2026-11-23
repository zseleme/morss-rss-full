import pytest

from morss import crawler
from morss.caching import RedisCacheHandler


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, data):
        self.store[key] = data


@pytest.fixture
def redis_cache():
    cache = RedisCacheHandler.__new__(RedisCacheHandler)  # skip connecting
    cache.r = FakeRedis()
    return cache


def test_redis_miss_raises_keyerror(redis_cache):
    with pytest.raises(KeyError):
        redis_cache['missing']
    assert 'missing' not in redis_cache


def test_redis_roundtrip(redis_cache):
    redis_cache['k'] = b'v'
    assert redis_cache['k'] == b'v'
    assert 'k' in redis_cache


def test_crawler_cache_miss_with_redis(redis_cache):
    # used to crash with pickle.loads(None) -> TypeError
    assert crawler.CacheHandler(cache=redis_cache).load('http://example.com/') is None
