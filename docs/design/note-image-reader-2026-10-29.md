# Original-image institution note reader (2026-10-29)

The user's screenshot showed a document reader containing a 132x132 cropped thumbnail and a large empty card. The same formal-site Marvell/Lumentum note at 2026-10-29 12:01:59 was reproduced. Its feed image was 800x2120 and its actual original was 951x2520: the document was available, but the reader reused feed-thumbnail rendering.

Changes:

- Resolve all attachments using original-image URLs first, with per-page preview fallback only when no original URL exists. Do not lose attachments when the original-image array is longer than the thumbnail array.
- Show sequential, full-width document pages at their natural aspect ratio in the note reader. Image-bearing readers use a wider document canvas; text-only note width and text parsing are preserved.
- Give each page a page index, an explicit enlarge action, load feedback and a retry action. The first original loads eagerly and later pages load lazily.
- Remove the empty body wrapper when a note contains only a repeated title and images.
- The original-image viewer has a stable top toolbar with return, bounded 50-300 percent enlargement, whole-image preview/fit-width toggle and original download. The image area supports both axes of scrolling; the toolbar remains outside that scroll host.
- Make the underlying note inert during image viewing, trap keyboard focus within the image viewer, then restore the image trigger. Escape closes the image first, then the note. Notes retain their existing list position and focus restoration.
- The mobile toolbar has two deliberate rows; the original-reading label stays on the first row and does not break into individual characters.

Validation:

Frozen from 9387f3b and compared with the previous production source manifest: 415 inputs, only the intended three source/test files changed. TypeScript and CRA build passed. Twenty-seven tests in three suites passed, including original-vs-thumbnail selection, full-only additional attachments, loading/error/retry, zoom controls, nested focus/Escape, existing search highlighting, taxonomy and assistant interaction.

Real-data local candidate used the exact same Marvell note. Default original rendered 958x2538.55 with source dimensions 951x2520. At 320px, the document image was 284x752.55 with no document-level horizontal overflow. At 150 percent the nested image area had clientWidth314/scrollWidth464, proving horizontal enlargement is reachable. No artificial image enlargement or OCR content was substituted for the actual source.

Evidence is under artifacts/note-image-reader-20261029. Final production version, screenshots and checks will be appended after deployment.

## Production acceptance

Published static/js/main.0051656d.js; index SHA256 75beee9f86d572db2b0b5e809caf84cf3e2be94e52c3a13cb2e12752a5a4dee9. Backup /root/deepfocus-note-image-reader-web-20261029-122649.tgz. Four release locks and previous index/main checks;276 disk and234 HTTP asset hashes passed;home,ontology,ai-fund200. Backend and standalone simulation page unchanged.

The same real member Marvell note rendered its951x2520 original at958x2538.55, complete=true. The viewer at125 percent rendered1200px. A physical click on the visible document (not locator-forced centering) preserved reader scrollTop720 before and after image viewing. At320x740 the inline original was284x752.55 with zero horizontal document overflow;150-percent viewer clientWidth314/scrollWidth464 and keyboard ArrowRight moved scrollLeft23. Whole-image preview height584.98/top115/bottom699.98 within740px. Escape closed image then note, retaining Marvell search. Browser errors empty. Temporary viewport restored.

localhost3017 serves the same frozen production bundle;3020 is the candidate. Final screenshots include formal desktop inline document, phone inline document and phone150-percent viewer.
