{
  description = "Confer confidential worker image build tools";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        imageToolPackages = [
          pkgs.cryptsetup
          pkgs.dosfstools
          pkgs.e2fsprogs
          pkgs.mtools
          pkgs.squashfsTools
          pkgs.zstd
        ];
        mkosiSandbox = pkgs.mkosi-full.override {
          extraDeps = imageToolPackages;
        };
        mkosiTools = pkgs.buildEnv {
          name = "confer-worker-image-tools";
          paths = mkosiSandbox.dependencies;
          pathsToLink = [ "/bin" ];
          ignoreCollisions = true;
        };
        mkosi = pkgs.writeShellApplication {
          name = "mkosi";
          text = ''
            exec ${mkosiSandbox}/bin/mkosi \
              --extra-search-path=${mkosiTools}/bin \
              "$@"
          '';
        };
      in {
        devShells.default = pkgs.mkShell {
          name = "confer-worker-image";
          buildInputs = [
            mkosi
            pkgs.qemu
            pkgs.qemu-utils
          ] ++ imageToolPackages ++ [
            pkgs.apt
            pkgs.dpkg
            pkgs.debootstrap
            pkgs.gnupg
            pkgs.git
            pkgs.gnumake
            pkgs.coreutils
            pkgs.util-linux
            pkgs.binutils
            pkgs.gzip
            pkgs.xz
          ];

          shellHook = ''
            if [[ $- == *i* ]]; then
              echo "Confer confidential worker image tools"
              echo "mkosi: $(mkosi --version)"
              echo "cc:    $(cc --version | head -1)"
            fi
          '';
        };

        packages.default = mkosi;
      });
}
