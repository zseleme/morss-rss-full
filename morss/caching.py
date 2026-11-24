# This file is part of morss
#
# Copyright (C) 2013-2020 pictuga <contact@pictuga.com>
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.

import os
import threading
import time
from collections import OrderedDict

CACHE_SIZE = int(os.getenv('CACHE_SIZE', 1000)) # max number of items in cache (default: 1k items)
CACHE_LIFESPAN = int(os.getenv('CACHE_LIFESPAN', 60)) # how often to auto-clear the cache (default: 1min)
DISKCACHE_SIZE = int(os.getenv('DISKCACHE_SIZE', 100 * 1024 * 1024)) # diskcache size limit, in bytes (default: 100MB)
REDIS_TTL = int(os.getenv('REDIS_TTL', 7 * 24 * 60 * 60)) # redis entries expiry, in sec (default: 7 days, 0: never)


class BaseCache:
    """ Subclasses must behave like a dict """

    def trim(self):
        pass

    def autotrim(self, delay=CACHE_LIFESPAN):
        # trim the cache every so often

        self.trim()

        t = threading.Timer(delay, self.autotrim)
        t.daemon = True
        t.start()

    def __contains__(self, url):
        try:
            self[url]

        except KeyError:
            return False

        else:
            return True


class CappedDict(OrderedDict, BaseCache):
    def trim(self):
        if CACHE_SIZE >= 0:
            for i in range( max( len(self) - CACHE_SIZE , 0 )):
                self.popitem(False)

    def __setitem__(self, key, data):
        # https://docs.python.org/2/library/collections.html#ordereddict-examples-and-recipes
        if key in self:
            del self[key]
        OrderedDict.__setitem__(self, key, data)

        # enforce the cap on every write: autotrim's timer thread is started
        # at import, i.e. in gunicorn's master with --preload, and threads
        # don't survive fork, so the workers (which hold the cache) never trim
        self.trim()


try:
    import redis # isort:skip
except ImportError:
    pass


class RedisCacheHandler(BaseCache):
    def __init__(self, host='localhost', port=6379, db=0, password=None, ttl=None):
        self.r = redis.Redis(host=host, port=port, db=db, password=password)
        self.ttl = ttl

    def __getitem__(self, key):
        data = self.r.get(key)

        if data is None:
            # behave like a dict: callers (e.g. crawler.CacheHandler.load)
            # rely on KeyError for cache misses
            raise KeyError(key)

        return data

    def __setitem__(self, key, data):
        # without an expiry, entries (e.g. one article body per extracted URL)
        # would pile up forever: redis has no size cap of its own here
        # redis rejects ex <= 0: 0 (or a negative value) means "never expire"
        self.r.set(key, data, ex=self.ttl if self.ttl and self.ttl > 0 else None)


try:
    import diskcache # isort:skip
except ImportError:
    pass


class DiskCacheHandler(BaseCache):
    def __init__(self, directory=None, **kwargs):
        self.cache = diskcache.Cache(directory=directory, eviction_policy='least-frequently-used', **kwargs)

    def __del__(self):
        self.cache.close()

    def trim(self):
        self.cache.cull()

    def __getitem__(self, key):
        return self.cache[key]

    def __setitem__(self, key, data):
        self.cache.set(key, data)


def make_cache(backend):
    """ Cache for the CACHE env var: 'redis', 'diskcache' or 'memory' (also
    used when unset). Anything else is a config error, reported as such
    rather than as a NameError on default_cache. """
    backend = (backend or 'memory').strip().lower()

    if backend == 'redis':
        return RedisCacheHandler(
            host = os.getenv('REDIS_HOST', 'localhost'),
            port = int(os.getenv('REDIS_PORT', 6379)),
            db = int(os.getenv('REDIS_DB', 0)),
            password = os.getenv('REDIS_PWD', None),
            ttl = REDIS_TTL
        )

    elif backend == 'diskcache':
        return DiskCacheHandler(
            directory = os.getenv('DISKCACHE_DIR', '/tmp/morss-diskcache'),
            size_limit = DISKCACHE_SIZE # in bytes (CACHE_SIZE is an item count)
        )

    elif backend == 'memory':
        return CappedDict()

    raise ValueError('Invalid CACHE=%r (expected redis, diskcache or memory)' % backend)


default_cache = make_cache(os.getenv('CACHE'))
