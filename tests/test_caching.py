import pytest

from morss import crawler
from morss.caching import RedisCacheHandler


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.expiry = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, data, ex=None):
        self.store[key] = data
        self.expiry[key] = ex


@pytest.fixture
def redis_cache():
    cache = RedisCacheHandler.__new__(RedisCacheHandler)  # skip connecting
    cache.r = FakeRedis()
    cache.ttl = 3600
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


def test_redis_entries_expire(redis_cache):
    redis_cache['k'] = b'v'
    assert redis_cache.r.expiry['k'] == 3600


def test_redis_ttl_zero_never_expires(redis_cache):
    redis_cache.ttl = 0
    redis_cache['k'] = b'v'
    assert redis_cache.r.expiry['k'] is None


def test_diskcache_keeps_article_sized_entries(tmp_path):
    from morss.caching import DISKCACHE_SIZE, DiskCacheHandler
    cache = DiskCacheHandler(directory=str(tmp_path), size_limit=DISKCACHE_SIZE)
    for i in range(20):
        cache['article-%d' % i] = b'x' * 20000  # typical article body
    cache.trim()
    assert all('article-%d' % i in cache for i in range(20))
    cache.cache.close()


def test_capped_dict_enforces_cap_on_write(monkeypatch):
    # no autotrim timer involved: gunicorn workers never get one
    from morss import caching
    monkeypatch.setattr(caching, 'CACHE_SIZE', 3)
    cache = caching.CappedDict()
    for i in range(5):
        cache['k%d' % i] = b'v'
    assert list(cache) == ['k2', 'k3', 'k4']

    cache['k2'] = b'v2'  # rewriting moves it to the newest position
    cache['k5'] = b'v'
    assert list(cache) == ['k4', 'k2', 'k5']  # k3 was the oldest


def test_redis_negative_ttl_never_expires(redis_cache):
    # redis rejects ex <= 0 ("invalid expire time"): every write would fail
    redis_cache.ttl = -1
    redis_cache['k'] = b'v'
    assert redis_cache.r.expiry['k'] is None


@pytest.mark.parametrize('backend', [None, '', 'memory', ' Memory '])
def test_make_cache_memory(backend):
    from morss import caching
    assert isinstance(caching.make_cache(backend), caching.CappedDict)


def test_make_cache_rejects_unknown_backend():
    from morss import caching
    with pytest.raises(ValueError, match='CACHE'):
        caching.make_cache('reddis')


def test_crawler_uses_swapped_default_cache(monkeypatch):
    # process(cache=...) swaps caching.default_cache after crawler is imported
    from morss import caching
    swapped = caching.CappedDict()  # empty, i.e. falsy
    monkeypatch.setattr(caching, 'default_cache', swapped)
    assert crawler.CacheHandler().cache is swapped


def test_capped_dict_lock_is_reset_after_fork(monkeypatch):
    # a worker forked while the parent's lock was held must get a free lock
    from morss import caching
    monkeypatch.setattr(caching.CappedDict, '_lock', caching.CappedDict._lock)
    held = caching.CappedDict._lock
    held.acquire()
    try:
        caching._reset_lock_after_fork()
        assert caching.CappedDict._lock is not held
        assert caching.CappedDict._lock.acquire(blocking=False)
        caching.CappedDict._lock.release()
    finally:
        held.release()


def test_capped_dict_concurrent_writes(monkeypatch):
    # gthread worker: several requests write to the same cache at once
    import threading

    from morss import caching
    monkeypatch.setattr(caching, 'CACHE_SIZE', 50)
    cache = caching.CappedDict()
    errors = []

    def writer(n):
        try:
            for i in range(2000):
                cache['k%d' % (i % 80)] = b'%d' % n
                cache.get('k%d' % ((i + n) % 80))
        except Exception as e:  # pylint: disable=broad-except
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(cache) <= 50
