# 0003 — OpenCV fallback for video frames when FFmpeg is absent

## Context

Video posts need a representative, non-black frame with a timestamp and source
link for the print edition. The target Windows machine did not have FFmpeg
installed, and requiring a system binary would block the fixture demo and add an
untested install step.

## Decision

- Use **OpenCV (`opencv-python-headless`) as the baseline** for frame extraction.
  It is a pinned Python dependency (`video` extra) with no system binary.
- Sample the video, reject black/near-black frames, choose the best candidate and
  record its timestamp and source message.
- Preserve the original video and store extracted frames as reversible
  derivatives.
- If extraction fails, omit the frame rather than print a black box or a
  fabricated image.
- Keep FFmpeg optional for future higher-quality extraction; do not silently
  pretend it is present.

## Alternatives considered

- **Require FFmpeg**: better codec coverage but an extra external install and
  PATH troubleshooting on Windows.
- **ImageIO/ffmpeg-python**: still needs a binary.
- **No video support**: fails the media acceptance gate.

## Consequences

- The fixture video (`exercise.mp4`) yields a meaningful frame without system
  tools.
- Codec support is whatever OpenCV’s bundled backend supports; unusual formats may
  fail and are reported rather than faked.
- `diagnostics` reports “FFmpeg not found (OpenCV fallback used for video frames)”.

## Files

- `channel_press/media/service.py`
- `fixtures/media/exercise.mp4`
- `tests/test_media_graphics.py`
