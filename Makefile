.PHONY: help test test-linux-policy build validate-gcp-image gcp-archive prepare-release clean

SHELL_SCRIPTS := \
	mkosi.postinst.chroot \
	mkosi.prepare \
	mkosi.skeleton/usr/local/libexec/confer-worker-config \
	mkosi.skeleton/usr/local/libexec/confer-worker-health \
	scripts/create-release-manifest \
	tests/test-guest-scripts.sh \
	tests/validate-gcp-image.sh \
	tests/test-linux-policy.sh

IMAGE_VERSION ?= $(shell sed -n 's/^ImageVersion=//p' mkosi.conf)
RAW_IMAGE := mkosi.output/confer-worker-image_$(IMAGE_VERSION).raw
GCP_ARCHIVE := confer-worker-image_$(IMAGE_VERSION).tar.gz

help:
	@echo "make test         Test the one-shot worker listener"
	@echo "make test-linux-policy  Parse the guest sshd and nftables policy on Linux"
	@echo "make build        Build the measured worker disk image"
	@echo "make validate-gcp-image  Verify the built disk has its boot artifacts"
	@echo "make gcp-archive  Package disk.raw for Compute Engine import"
	@echo "make prepare-release  Build the archive, measurements, and manifest"
	@echo "make clean        Remove generated build artifacts"

test:
	bash -n $(SHELL_SCRIPTS)
	bash tests/test-guest-scripts.sh
	PYTHONDONTWRITEBYTECODE=1 \
		python3 -m unittest discover -s tests -p 'test_*.py'

test-linux-policy:
	test "$$(uname -s)" = Linux
	bash tests/test-linux-policy.sh

build:
	sudo $$(which mkosi) --force --image-version=$(IMAGE_VERSION)

validate-gcp-image: build
	bash tests/validate-gcp-image.sh $(RAW_IMAGE)

gcp-archive: validate-gcp-image
	rm -rf .gcp-stage
	mkdir -p .gcp-stage
	cp $(RAW_IMAGE) .gcp-stage/disk.raw
	tar --format=oldgnu \
		--sort=name \
		--mtime=@0 \
		--owner=0 \
		--group=0 \
		--numeric-owner \
		-C .gcp-stage \
		-Scf - disk.raw | gzip -n > $(GCP_ARCHIVE)
	rm -rf .gcp-stage
	gzip --test $(GCP_ARCHIVE)
	test "$$(tar -tzf $(GCP_ARCHIVE))" = disk.raw
	test "$$(sha256sum $(RAW_IMAGE) | cut -d' ' -f1)" = "$$(tar -xOzf $(GCP_ARCHIVE) disk.raw | sha256sum | cut -d' ' -f1)"
	@echo "Created $(GCP_ARCHIVE)"

prepare-release: gcp-archive
	sha256sum $(GCP_ARCHIVE) > SHA256SUMS
	scripts/measure-release-image $(RAW_IMAGE) \
		| scripts/create-release-manifest $(IMAGE_VERSION) \
		> manifest.json

clean:
	rm -rf mkosi.output mkosi.cache .gcp-stage
	rm -f confer-worker-image_*.raw confer-worker-image_*.qcow2
	rm -f confer-worker-image_*.tar.gz confer-worker-image_*.efi
	rm -f SHA256SUMS manifest.json manifest.bundle.json
