import importlib.machinery
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def load_measurement_script():
  path = REPOSITORY_ROOT / "scripts/measure-release-image"
  loader = importlib.machinery.SourceFileLoader(
      "measure_release_image_test",
      str(path))
  specification = importlib.util.spec_from_loader(loader.name, loader)
  if specification is None:
    raise AssertionError(f"Unable to load {path}")
  module = importlib.util.module_from_spec(specification)
  loader.exec_module(module)
  return module


class ReleaseMeasurementTest(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.measure = load_measurement_script()

  @staticmethod
  def create_gpt_image(path            : Path,
                       entry_size     : int = 128,
                       first_usable_lba: int = 34) -> tuple[bytes, bytes, bytes]:
    sector_size = 512
    disk = bytearray(64 * sector_size)
    entries = bytearray(4 * entry_size)
    first_entry = bytes(range(1, entry_size + 1))
    third_entry = bytes((index * 3 + 1) % 256 for index in range(entry_size))
    entries[:entry_size] = first_entry
    entries[2 * entry_size:3 * entry_size] = third_entry

    header = bytearray(struct.pack(
        "<8sIIIIQQQQ16sQIII",
        b"EFI PART",
        0x00010000,
        92,
        0,
        0,
        1,
        63,
        first_usable_lba,
        60,
        bytes(range(16)),
        2,
        4,
        entry_size,
        zlib.crc32(entries) & 0xffffffff))
    struct.pack_into("<I", header, 16, zlib.crc32(header) & 0xffffffff)
    disk[sector_size:sector_size + len(header)] = header
    disk[2 * sector_size:2 * sector_size + len(entries)] = entries
    path.write_bytes(disk)
    return bytes(header), first_entry, third_entry

  def test_gpt_event_matches_the_edk2_serialization(self):
    with tempfile.TemporaryDirectory() as directory:
      image = Path(directory) / "disk.raw"
      header, first_entry, third_entry = self.create_gpt_image(image)

      event = self.measure.get_gpt_event_data(image, 512)

    self.assertEqual(
        event,
        header + struct.pack("<Q", 2) + first_entry + third_entry)

  def test_gpt_checksums_are_required(self):
    with tempfile.TemporaryDirectory() as directory:
      image = Path(directory) / "disk.raw"
      self.create_gpt_image(image)
      disk = bytearray(image.read_bytes())
      disk[512 + 56] ^= 1
      image.write_bytes(disk)

      with self.assertRaisesRegex(
          self.measure.ReleaseMeasurementError,
          "header checksum"):
        self.measure.get_gpt_event_data(image, 512)

  def test_gpt_header_must_match_edk2_constraints(self):
    with tempfile.TemporaryDirectory() as directory:
      image = Path(directory) / "disk.raw"
      for name, entry_size, first_usable_lba in [
          ("entry size is not a power of two", 136, 34),
          ("first usable LBA overlaps entries", 128, 2),
      ]:
        with self.subTest(name=name):
          self.create_gpt_image(
              image,
              entry_size=entry_size,
              first_usable_lba=first_usable_lba)
          with self.assertRaisesRegex(
              self.measure.ReleaseMeasurementError,
              "unsupported"):
            self.measure.get_gpt_event_data(image, 512)

  @staticmethod
  def create_non_aligned_pe() -> bytes:
    image = bytearray(0x401)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3c, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into(
        "<HHIIIHH",
        image,
        0x84,
        0x8664,
        1,
        0,
        0,
        0,
        240,
        0x22)

    optional_header = 0x98
    struct.pack_into("<H", image, optional_header, 0x20b)
    struct.pack_into("<I", image, optional_header + 4, 0x200)
    struct.pack_into("<I", image, optional_header + 16, 0x1000)
    struct.pack_into("<I", image, optional_header + 20, 0x1000)
    struct.pack_into("<Q", image, optional_header + 24, 0x400000)
    struct.pack_into("<I", image, optional_header + 32, 0x1000)
    struct.pack_into("<I", image, optional_header + 36, 0x200)
    struct.pack_into("<I", image, optional_header + 56, 0x2000)
    struct.pack_into("<I", image, optional_header + 60, 0x200)
    struct.pack_into("<H", image, optional_header + 68, 10)
    struct.pack_into("<Q", image, optional_header + 72, 0x100000)
    struct.pack_into("<Q", image, optional_header + 80, 0x1000)
    struct.pack_into("<Q", image, optional_header + 88, 0x100000)
    struct.pack_into("<Q", image, optional_header + 96, 0x1000)
    struct.pack_into("<I", image, optional_header + 108, 16)

    section_header = 0x188
    image[section_header:section_header + 8] = b".text\0\0\0"
    struct.pack_into(
        "<IIIIIIHHI",
        image,
        section_header + 8,
        1,
        0x1000,
        0x200,
        0x200,
        0,
        0,
        0,
        0,
        0x60000020)
    for index in range(0x200):
      image[0x200 + index] = (index * 17 + 3) % 256
    image[0x400] = 0xa5
    return bytes(image)

  def test_uki_digest_matches_the_unpadded_edk2_golden(self):
    with tempfile.TemporaryDirectory() as directory:
      uki = Path(directory) / "BOOTX64.EFI"
      uki.write_bytes(self.create_non_aligned_pe())

      digest = self.measure.calculate_authenticode_digest(
          uki.read_bytes(),
          "test UKI")

    self.assertEqual(
        digest.hex(),
        # Independently cross-checked with LIEF 0.17.6
        # Binary.authentihash(ALGORITHMS.SHA_384).
        "0dcfb973dc7c1361c05fc0044c77cd67b01a42fa225e2453df5c4dfa"
        "152e485014415af8d45dfae5ac9064ea3fe44179")
    self.assertNotEqual(
        digest.hex(),
        # tdx-measure v0.0.10's synthetic 8-byte padding changes this
        # deliberately non-aligned fixture and does not match pinned EDK2.
        "7b2ff6e9097016fb0757b65ac17a854635ddbbdb1a02b77f0885655f6"
        "093f0895b83255bb105c9ade85fda5b5c54002")

  def test_uki_checksum_and_certificate_do_not_change_the_digest(self):
    unsigned = bytearray(self.create_non_aligned_pe()[:0x400])
    original_digest = self.measure.calculate_authenticode_digest(
        unsigned,
        "test UKI")
    struct.pack_into("<I", unsigned, 0x98 + 64, 0x12345678)
    self.assertEqual(
        self.measure.calculate_authenticode_digest(unsigned, "test UKI"),
        original_digest)

    certificate = bytes(range(16))
    signed = unsigned + certificate
    struct.pack_into("<II", signed, 0x98 + 144, 0x400, len(certificate))
    signed_digest = self.measure.calculate_authenticode_digest(
        signed,
        "test UKI")
    self.assertEqual(
        signed_digest.hex(),
        "1d1ca51699b2703cc6629043ca9300d73f1c16fe7ecf1fd24fac2f7059"
        "bf9e8a2c587ffe9d9d9715555f4cfa01812455")
    signed[-1] ^= 0xff
    self.assertEqual(
        self.measure.calculate_authenticode_digest(signed, "test UKI"),
        signed_digest)

  def test_uki_rejects_malformed_hashed_regions(self):
    image = bytearray(self.create_non_aligned_pe())
    section_header = 0x188
    struct.pack_into("<I", image, section_header + 20, len(image))

    with self.assertRaisesRegex(
        self.measure.ReleaseMeasurementError,
        "section is outside"):
      self.measure.calculate_authenticode_digest(image, "test UKI")

  def test_uki_rejects_section_table_outside_headers(self):
    image = bytearray(self.create_non_aligned_pe())
    struct.pack_into("<I", image, 0x98 + 60, 0x1af)

    with self.assertRaisesRegex(
        self.measure.ReleaseMeasurementError,
        "headers are invalid"):
      self.measure.calculate_authenticode_digest(image, "test UKI")

  def test_uki_rejects_overlapping_raw_sections(self):
    image = bytearray(self.create_non_aligned_pe())
    struct.pack_into("<H", image, 0x84 + 2, 2)
    section_header = 0x1b0
    image[section_header:section_header + 8] = b".data\0\0\0"
    struct.pack_into(
        "<IIIIIIHHI",
        image,
        section_header + 8,
        1,
        0x2000,
        0x100,
        0x300,
        0,
        0,
        0,
        0,
        0xc0000040)
    struct.pack_into("<I", image, 0x98 + 56, 0x3000)

    with self.assertRaisesRegex(
        self.measure.ReleaseMeasurementError,
        "raw sections overlap"):
      self.measure.calculate_authenticode_digest(image, "test UKI")

  def test_extracts_the_embedded_linux_efi_application_by_virtual_size(self):
    linux_image = self.create_non_aligned_pe()
    uki = bytearray(self.create_non_aligned_pe()[:0x200]) + linux_image
    section_header = 0x188
    uki[section_header:section_header + 8] = b".linux\0\0"
    struct.pack_into("<I", uki, section_header + 8, len(linux_image))
    struct.pack_into("<I", uki, section_header + 16, len(linux_image))

    self.assertEqual(
        self.measure.extract_embedded_linux_image(bytes(uki)),
        linux_image)

  def test_rtmr_transcript_has_a_fixed_golden_result(self):
    measurements = self.measure.calculate_measurements(
        bytes(range(48)),
        bytes(range(48, 96)),
        bytes(range(96, 144)))

    self.assertEqual(measurements, {
        "mrtd": (
            "c1ee9c16e3afc506cfe042c5b846a368528f3b37618eafb2"
            "7469bc114cf914e9222c91618470e7f2b28ac360968270a5"),
        "rtmr0": (
            "c49d22aff6edb37cb6178defb05e0e2b512c26960e6ee73b"
            "1ea303365a31def807ab2ad71e5874236feca2ca552c6307"),
        "rtmr1": (
            "cc223af4034ff1d14ba1e937b6d822b068143fc248f357d5"
            "4be99b85552d84dc7fdb69bf9259dce6a1bf01646e4c5613"),
        "rtmr2": "0" * 96,
    })

  def test_rtmr_inputs_must_be_sha384_digests(self):
    for gpt_digest, uki_digest, linux_digest in [
        (bytes(47), bytes(48), bytes(48)),
        (bytes(48), bytes(49), bytes(48)),
        (bytes(48), bytes(48), bytes(47)),
    ]:
      with self.subTest(
          gpt_bytes=len(gpt_digest),
          uki_bytes=len(uki_digest)), self.assertRaises(
              self.measure.ReleaseMeasurementError):
        self.measure.calculate_measurements(
            gpt_digest,
            uki_digest,
            linux_digest)

  def test_platform_profile_replays_the_normalized_ccel_fixture(self):
    profile = self.measure.load_platform_profile()

    self.assertEqual(profile["rtmr0"], (
        "c49d22aff6edb37cb6178defb05e0e2b512c26960e6ee73b"
        "1ea303365a31def807ab2ad71e5874236feca2ca552c6307"))
    self.assertEqual(len(profile["rtmr0Fixture"]["events"]), 14)

  def test_platform_profile_derives_rtmr0_from_the_fixture(self):
    original = self.measure.load_platform_profile()
    profile = json.loads((
        REPOSITORY_ROOT / "release/gcp-tdx-platform-profile.json"
    ).read_text(encoding="utf-8"))
    profile["rtmr0Fixture"]["events"][0]["digest"] = "0" * 96

    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "profile.json"
      path.write_text(json.dumps(profile), encoding="utf-8")
      changed = self.measure.load_platform_profile(path)

    self.assertNotEqual(changed["rtmr0"], original["rtmr0"])


if __name__ == "__main__":
  unittest.main()
