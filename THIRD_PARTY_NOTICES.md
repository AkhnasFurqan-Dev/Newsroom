# Third-party notices

Newsroom is proprietary software, but it uses third-party components. Those components remain under their own licenses and notices. The root [LICENSE](LICENSE) applies only to the original Newsroom materials and does not replace, narrow or override any third-party license.

This file summarizes the main components observed in the project and the packaged Windows build. It is not a complete license bundle and is not legal advice. The full license text shipped with each component controls. Where a package's metadata is ambiguous, the component's own license file and the accompanying `packaging/sbom.spdx.json` should be checked before redistribution.

## Main components

| Component | Typical use | Declared license |
|---|---|---|
| CPython 3.12 | Bundled Python runtime | Python Software Foundation License 2.0 |
| FastAPI | Local web application | MIT |
| Starlette | Web application foundation | BSD-3-Clause |
| Uvicorn | Local application server | BSD-3-Clause |
| SQLAlchemy | Database access | MIT |
| Alembic | Database migrations | MIT |
| Pydantic | Data validation | MIT |
| Jinja2 | Server-rendered pages | BSD-3-Clause |
| python-multipart | Form uploads | Apache-2.0 |
| argon2-cffi | Password hashing | MIT |
| itsdangerous | Signed values | BSD-3-Clause |
| HTTPX | HTTP client | BSD-3-Clause |
| Pillow | Image processing | HPND / MIT-CMU |
| qrcode | QR code generation | BSD |
| PyYAML | Configuration parsing | MIT |
| python-dateutil | Date parsing | BSD-3-Clause / Apache-2.0 (dual) |
| tzdata | Time-zone data | Apache-2.0 |
| Playwright | Browser automation for print rendering | Apache-2.0 |
| PyMuPDF | PDF inspection and rendering | AGPL-3.0-only |
| pypdf | PDF handling | BSD-3-Clause |
| fontTools | Font inspection and embedding | MIT |
| Telethon | Optional Telegram client | MIT |
| OpenCV | Optional video-frame extraction | Apache-2.0 |
| pywebview | Windows desktop shell | BSD-3-Clause |
| PyInstaller | Windows packaging | GPL-2.0-or-later with the PyInstaller bootloader exception |
| Paged.js | Print pagination in the renderer | MIT |
| Chromium | Bundled print/e-paper renderer | BSD-style Chromium license and its third-party notices |
| FFmpeg | Bundled media helper used by Playwright | LGPL-2.1-or-later (check the exact build) |
| Microsoft Edge WebView2 | Desktop window runtime | Microsoft terms for the WebView2 Runtime |
| Ollama | Optional local model runtime | MIT (separate installation; not bundled) |
| Qwen3.5 model weights | Optional local drafting models | Apache-2.0 (separate download; not bundled) |
| Natural Earth | Bundled map data | Public domain |
| Bitstream Charter | Outlines in the Newsroom logo | Bitstream Charter license |
| JavaScript dependencies in `node_modules` | Paged.js build and supporting packages | Each package's own license file |

## Important distribution note: PyMuPDF

The installed PyMuPDF package declares the GNU Affero General Public License, version 3 (AGPL-3.0-only). The current packaged build includes that dependency.

The root Newsroom license does not override the AGPL. If Newsroom or its installer is distributed to other people as a closed-source product, the AGPL may impose source-disclosure, notice and other obligations on the combined distribution. That question needs legal review, or the PDF component needs to be replaced with a component whose license is compatible with the intended distribution. This notice flags the issue; it does not resolve it.

## Fonts and brand assets

The Newsroom logo files contain outlined lettering, not a live font. The brand documentation states that Bitstream Charter's license permits commercial use and redistribution, but the font itself should be reviewed before it is shipped as a font file. Fonts used inside a customer's newspaper are the publisher's responsibility; Newsroom records and embeds the fonts selected for an edition.

## What this file does not do

- It does not grant any right to use Newsroom's original code, interface, artwork, documentation or brand assets. See [LICENSE](LICENSE).
- It does not replace the license text distributed with a third-party component.
- It does not certify that the current packaged build is ready for public distribution. The PyMuPDF and Chromium/WebView2 obligations should be reviewed with counsel before any external release.
