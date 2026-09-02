import json
import subprocess
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CREATE_MANIFEST = REPOSITORY_ROOT / "scripts/create-release-manifest"


class ReleaseManifestTest(unittest.TestCase):
  def setUp(self):
    self.image_version = "1.2.3-RC1"
    self.mrtd = "d" * 96
    self.rtmr0 = "c" * 96
    self.rtmr1 = "b" * 96
    self.rtmr2 = "0" * 96

  def create_manifest(self) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            CREATE_MANIFEST,
            self.image_version,
        ],
        check=False,
        capture_output=True,
        input=json.dumps({
            "mrtd": self.mrtd,
            "rtmr0": self.rtmr0,
            "rtmr1": self.rtmr1,
            "rtmr2": self.rtmr2,
        }),
        text=True)

  def test_creates_the_canonical_worker_release_manifest(self):
    result = self.create_manifest()

    self.assertEqual(result.returncode, 0, result.stderr)
    expected = {
        "artifactType": "confer-worker-image",
        "imageVersion": self.image_version,
        "tdxMeasurements": {
            "mrtd": self.mrtd,
            "rtmr0": self.rtmr0,
            "rtmr1": self.rtmr1,
            "rtmr2": self.rtmr2,
        },
        "version": 1,
    }
    self.assertEqual(
        result.stdout,
        json.dumps(expected, separators=(",", ":"), sort_keys=True) + "\n")

  def test_rejects_noncanonical_fields(self):
    invalid = [
        ("image version", "1.2", self.mrtd, self.rtmr0, "b" * 96, "0" * 96),
        ("MRTD uppercase", "1.2.3", self.mrtd.upper(), self.rtmr0, "b" * 96,
         "0" * 96),
        ("RTMR uppercase", "1.2.3", self.mrtd, self.rtmr0, "B" * 96, "0" * 96),
        ("RTMR short", "1.2.3", self.mrtd, self.rtmr0, "b" * 95, "0" * 96),
    ]

    for name, image_version, mrtd, rtmr0, rtmr1, rtmr2 in invalid:
      with self.subTest(name=name):
        self.image_version = image_version
        self.mrtd = mrtd
        self.rtmr0 = rtmr0
        self.rtmr1 = rtmr1
        self.rtmr2 = rtmr2
        self.assertNotEqual(self.create_manifest().returncode, 0)


if __name__ == "__main__":
  unittest.main()
