import asyncio
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from noob_gateway.control_lease import (  # noqa: E402
    ControlLease,
    LeaseInvalid,
    LeaseReleaseRequired,
)


class FakeClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class ControlLeaseExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_after_ttl_latches_without_consuming_release(self):
        clock = FakeClock()
        lease = ControlLease(5.0, clock=clock)
        await lease.claim()
        clock.advance(5.1)

        first = await lease.snapshot()
        second = await lease.snapshot()
        self.assertFalse(first.active)
        self.assertTrue(first.release_required)
        self.assertTrue(second.release_required)
        self.assertTrue(await lease.expire_if_needed())
        self.assertFalse(await lease.expire_if_needed())

    async def test_validate_after_ttl_preserves_release_obligation(self):
        clock = FakeClock()
        lease = ControlLease(5.0, clock=clock)
        lease_id = await lease.claim()
        clock.advance(5.1)

        with self.assertRaises(LeaseInvalid):
            await lease.validate(lease_id)
        self.assertTrue(await lease.expire_if_needed())

    async def test_renew_after_ttl_preserves_release_obligation(self):
        clock = FakeClock()
        lease = ControlLease(5.0, clock=clock)
        lease_id = await lease.claim()
        clock.advance(5.1)

        with self.assertRaises(LeaseInvalid):
            await lease.renew(lease_id)
        with self.assertRaises(LeaseReleaseRequired):
            await lease.claim()
        self.assertTrue(await lease.expire_if_needed())
        self.assertIsInstance(await lease.claim(), str)

    async def test_release_obligation_has_exactly_one_consumer(self):
        clock = FakeClock()
        lease = ControlLease(5.0, clock=clock)
        await lease.claim()
        clock.advance(5.1)

        results = await asyncio.gather(
            lease.expire_if_needed(),
            lease.expire_if_needed(),
            lease.expire_if_needed(),
        )
        self.assertEqual(results.count(True), 1)


if __name__ == "__main__":
    unittest.main()
