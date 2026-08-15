#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 /path/to/Blackmagic_DeckLink_SDK_16.0 [install-prefix]" >&2
  exit 64
fi

sdk_root="$(realpath "$1")"
install_prefix="${2:-/opt/scoresight-decklink}"
include_dir="${sdk_root}/Linux/include"
ffmpeg_version="8.1.2"
ffmpeg_signing_key="DD1EC9E8DE085C629B3E1846B18E8928B3948D64"

if [[ ! -f "${include_dir}/DeckLinkAPI.h" ]]; then
  echo "DeckLinkAPI.h was not found under ${include_dir}" >&2
  exit 66
fi

for command in curl g++ gcc gpg make pkg-config tar; do
  command -v "$command" >/dev/null || { echo "missing command: $command" >&2; exit 69; }
done

build_dir="$(mktemp -d)"
gnupg_home="$(mktemp -d)"
trap 'rm -rf "$build_dir" "$gnupg_home"' EXIT
chmod 700 "$gnupg_home"

archive="ffmpeg-${ffmpeg_version}.tar.xz"
curl --fail --location --proto '=https' --tlsv1.2 \
  "https://ffmpeg.org/releases/${archive}" --output "${build_dir}/${archive}"
curl --fail --location --proto '=https' --tlsv1.2 \
  "https://ffmpeg.org/releases/${archive}.asc" --output "${build_dir}/${archive}.asc"
GNUPGHOME="$gnupg_home" gpg --batch --keyserver hkps://keyserver.ubuntu.com \
  --recv-keys "$ffmpeg_signing_key"
actual_fingerprint="$(GNUPGHOME="$gnupg_home" gpg --with-colons --fingerprint "$ffmpeg_signing_key" | awk -F: '$1 == "fpr" {print $10; exit}')"
[[ "$actual_fingerprint" == "$ffmpeg_signing_key" ]] || { echo "unexpected signing key" >&2; exit 65; }
GNUPGHOME="$gnupg_home" gpg --batch --verify \
  "${build_dir}/${archive}.asc" "${build_dir}/${archive}"

tar -C "$build_dir" -xf "${build_dir}/${archive}"
cd "${build_dir}/ffmpeg-${ffmpeg_version}"
./configure \
  --prefix="$install_prefix" \
  --disable-debug \
  --disable-doc \
  --enable-decklink \
  --enable-gpl \
  --enable-libx264 \
  --enable-nonfree \
  --extra-cflags="-I${include_dir}" \
  --extra-cxxflags="-I${include_dir}"
make -j"$(nproc)"
sudo make install
"${install_prefix}/bin/ffmpeg" -hide_banner -devices | grep -q decklink
echo "DeckLink-enabled FFmpeg installed at ${install_prefix}/bin/ffmpeg"

