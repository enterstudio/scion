# Copyright 2017 ETH Zurich
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
:mod:`rev_cache` --- Cache for revocations
==========================================
"""
# Stdlib
import logging
import threading

# External
from prometheus_client import Counter, Gauge

# Exported metrics.
REVS_TOTAL = Gauge("rc_revs_total", "# of cached revocations", ["server_id", "isd_as"])
REVS_BYTES = Gauge("rc_revs_bytes", "RevCache memory usage", ["server_id", "isd_as"])
REVS_ADDED = Counter("rc_revs_added_total", "Total revocations added",
                     ["server_id", "isd_as"])
REVS_REMOVED = Counter("rc_revs_removed_total", "Total revocations removed",
                       ["server_id", "isd_as"])


def _mk_key(srev_info):
    """Returns the key for a SignedRevInfo object."""
    return (srev_info.rev_info().isd_as(), srev_info.rev_info().p.ifID)


class RevCache:
    """Thread-safe cache for revocations with auto expiration of entries."""

    def __init__(self, capacity=1000, labels=None):  # pragma: no cover
        """
        :param dict labels:
            Labels added to the exported metrics. The following labels are supported:
                - server_id: A unique identifier of the server that is exporting
                - isd_as: The ISD_AS of where the server is running
                - type: A generic label for the type of the revocations.
        """
        self._cache = {}
        self._lock = threading.RLock()
        self._capacity = capacity
        self._labels = labels
        if self._labels:
            self._init_metrics()

    def _init_metrics(self):  # pragma: no cover
        REVS_TOTAL.labels(**self._labels).set(0)
        REVS_BYTES.labels(**self._labels).set(0)
        REVS_ADDED.labels(**self._labels).inc(0)
        REVS_REMOVED.labels(**self._labels).inc(0)

    def __contains__(self, srev_info):  # pragma: no cover
        stored_info = self.get(_mk_key(srev_info))
        if not stored_info:
            return False
        return stored_info.rev_info() == srev_info.rev_info()

    def __getitem__(self, key):  # pragma: no cover
        return self.get(key)

    def get(self, key, default=None):
        with self._lock:
            try:
                rev_info = self._cache[key]
            except KeyError:
                return default
            if self._check_active(rev_info):
                return rev_info
            return default

    def values(self):
        """
        Return all active revocations
        :return: list(SignedRevInfo)
        """
        ret = []
        with self._lock:
            for v in list(self._cache.values()):
                if self._check_active(v):
                    ret.append(v)
        return ret

    def add(self, srev_info):
        """
        Adds srev_info to the cache and returns True if the operation succeeds.
        :param type: SignedRevInfo
        :return: boolean
        """
        if not srev_info.rev_info().active():
            return False
        with self._lock:
            key = _mk_key(srev_info)
            stored_info = self.get(key)
            if not stored_info:
                # Try to free up space in case the cache reaches the cap limit.
                if len(self._cache) >= self._capacity:
                    for info in list(self._cache.values()):
                        self._check_active(info)
                # Couldn't free up enough space...
                if len(self._cache) >= self._capacity:
                    logging.error("Revocation cache full!.")
                    return False
                self._cache[key] = srev_info
                if self._labels:
                    REVS_ADDED.labels(**self._labels).inc()
                    REVS_TOTAL.labels(**self._labels).inc()
                    REVS_BYTES.labels(**self._labels).inc(len(srev_info))
                return True
            if srev_info.rev_info().p.timestamp > stored_info.rev_info().p.timestamp:
                self._cache[key] = srev_info
                if self._labels:
                    REVS_ADDED.labels(**self._labels).inc()
                    REVS_REMOVED.labels(**self._labels).inc()
                    REVS_BYTES.labels(**self._labels).inc(len(srev_info) - len(stored_info))
                return True
            return False

    def _check_active(self, srev_info):  # pragma: no cover
        """
        Removes an expired revocation from the cache.
        :param type: SignedRevInfo
        :return: boolean
        """
        if not srev_info.rev_info().active():
            del self._cache[_mk_key(srev_info)]
            if self._labels:
                REVS_REMOVED.labels(**self._labels).inc()
                REVS_TOTAL.labels(**self._labels).dec()
                REVS_BYTES.labels(**self._labels).dec(len(srev_info))
            return False
        return True
