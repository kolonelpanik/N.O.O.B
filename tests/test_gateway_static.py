import json
import shutil
import subprocess
import unittest
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "gateway" / "noob_gateway" / "static" / "app.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for browser helper tests")
class GatewayStaticTests(unittest.TestCase):
    def test_operator_script_is_valid_javascript(self):
        result = subprocess.run(
            [shutil.which("node"), "--check", str(APP_JS)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_input_queue_preserves_order_and_stops_after_failure(self):
        script = r"""
const assert = require("node:assert/strict");
const {createSerializedInputQueue, leaseRenewInterval} = require(process.argv[1]);

(async () => {
  let active = true;
  let unblockDown;
  const downGate = new Promise((resolve) => { unblockDown = resolve; });
  const sent = [];
  const failures = [];
  const queue = createSerializedInputQueue(
    async (command) => {
      sent.push(command.id);
      if (command.id === "down") await downGate;
      if (command.id === "up") throw new Error("rate_limited");
    },
    async (error) => {
      failures.push(error.message);
      active = false;
    },
    () => active,
    8,
  );

  const down = queue.enqueue({id: "down"});
  const up = queue.enqueue({id: "up"});
  const later = queue.enqueue({id: "later"});
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(sent, ["down"]);
  unblockDown();
  assert.deepEqual(await Promise.all([down, up, later]), [true, false, false]);
  assert.deepEqual(sent, ["down", "up"]);
  assert.deepEqual(failures, ["rate_limited"]);
  assert.equal(queue.depth(), 0);
  assert.equal(leaseRenewInterval(5000), 2500);
  assert.equal(leaseRenewInterval(undefined), 2000);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script, str(APP_JS)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            result.returncode,
            0,
            json.dumps({"stdout": result.stdout, "stderr": result.stderr}),
        )


if __name__ == "__main__":
    unittest.main()
