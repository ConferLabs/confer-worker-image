#!/bin/bash
set -euo pipefail

image=${1:?Usage: validate-gcp-image.sh DISK_IMAGE}

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

validate_efi_application() {
  local application=$1
  local description

  if ! description=$(objdump -x "$application" 2>&1); then
    fail "Not a valid PE/COFF application: $application"
  fi
  grep -Fq "file format pei-x86-64" <<< "$description" ||
    fail "Not an x86-64 PE/COFF application: $application"
  grep -Eq 'Subsystem[[:space:]]+[0-9A-Fa-f]+[[:space:]]+\(EFI application\)' \
    <<< "$description" ||
    fail "Not an EFI application: $application"
}

validate_uki() {
  local uki=$1
  local output_directory=$2
  local section
  local section_file

  validate_efi_application "$uki"
  mkdir "$output_directory"
  for section in linux initrd cmdline osrel; do
    section_file=$output_directory/$section
    objcopy --dump-section ".$section=$section_file" "$uki" ||
      fail "UKI is missing .$section: $uki"
    [[ -s "$section_file" ]] || fail "UKI has an empty .$section: $uki"
  done

  tr -d '\0' < "$output_directory/cmdline" |
    grep -Eq '(^|[[:space:]])roothash=[0-9A-Fa-f]{64}([[:space:]]|$)' ||
    fail "UKI command line has no dm-verity root hash: $uki"
}

[[ -f "$image" ]] || fail "Disk image does not exist: $image"

partition_values=$(
  sfdisk --json "$image" |
    python3 -c '
import json
import sys

esp_type = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
partition_table = json.load(sys.stdin)["partitiontable"]
if partition_table["label"] != "gpt" or partition_table["unit"] != "sectors":
  raise SystemExit("Worker disk is not a sector-addressed GPT image")
partitions = partition_table["partitions"]
esp = [partition for partition in partitions
       if partition.get("type", "").upper() == esp_type]
if len(esp) != 1:
  raise SystemExit(f"Expected one EFI System Partition, found {len(esp)}")
print(esp[0]["start"], esp[0]["size"], partition_table["sectorsize"])
'
)
read -r esp_start esp_size sector_size <<< "$partition_values"

[[ "$esp_start" =~ ^[0-9]+$ ]] || fail "Invalid EFI partition start: $esp_start"
[[ "$esp_size" =~ ^[0-9]+$ ]] || fail "Invalid EFI partition size: $esp_size"
[[ "$sector_size" =~ ^[0-9]+$ ]] || fail "Invalid disk sector size: $sector_size"
((esp_size > 0)) || fail "EFI partition is empty"
((sector_size > 0)) || fail "Disk sector size is zero"

validation_root=$(mktemp -d)
trap 'rm -rf "$validation_root"' EXIT

esp_offset=$((esp_start * sector_size))
fat_image=$image@@$esp_offset

uki=$validation_root/BOOTX64.EFI
if ! mcopy \
  -i "$fat_image" \
  '::/EFI/BOOT/BOOTX64.EFI' \
  "$uki"; then
  fail "EFI fallback UKI is missing"
fi
[[ -s "$uki" ]] || fail "EFI fallback UKI is missing or empty"
validate_uki "$uki" "$validation_root/uki"

printf 'Validated direct EFI fallback UKI in %s\n' "$image"
