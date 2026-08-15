#!/usr/bin/env bash
set -euo pipefail

ffmpeg_bin="${FFMPEG_BIN:-/opt/scoresight-decklink/bin/ffmpeg}"
device="${DECKLINK_DEVICE:-DeckLink Mini Recorder HD}"
format="${DECKLINK_FORMAT:?set DECKLINK_FORMAT to a format code reported by probe-decklink.sh}"
output_fps="${OUTPUT_FPS:-30}"
rtsp_url="${RTSP_URL:-rtsp://127.0.0.1:8554/scoreboard}"
interlaced="${DECKLINK_INTERLACED:-false}"

filters="fps=${output_fps}"
if [[ "$interlaced" == "true" ]]; then
  filters="bwdif=mode=send_frame:parity=auto:deint=all,${filters}"
fi

exec "$ffmpeg_bin" -hide_banner -nostdin -loglevel warning \
  -thread_queue_size 4 \
  -f decklink -video_input sdi -format_code "$format" -i "$device" \
  -map 0:v:0 -an -vf "$filters" \
  -c:v libx264 -preset ultrafast -tune zerolatency \
  -pix_fmt yuv420p -g "$output_fps" -keyint_min "$output_fps" -bf 0 \
  -f rtsp -rtsp_transport tcp "$rtsp_url"

