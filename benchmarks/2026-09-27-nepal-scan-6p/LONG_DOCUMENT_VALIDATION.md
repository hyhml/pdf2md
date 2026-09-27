# Docling long-document validation

The Docling-backed skill was validated after the six-page comparison on a
116-page, 44 MiB Chinese scanned PDF containing the front matter and first
three chapters of the same book.

| Field | Result |
|---|---|
| Requested mode | `strong` |
| Selected stage | `strong` (Docling) |
| Status | `ok` |
| Input pages | 116 |
| Markdown page markers | 116, continuous from 1 through 116 |
| Empty/very short page blocks | 0 (minimum normalized block length: 169 characters) |
| Wall time | 441.64 seconds |
| Maximum RSS | 4,159,508 KiB |
| Output size | 338,085 bytes |
| Input SHA-256 | `bed52bdfb6be8aa17a98a452baedf87b41575b5ed0ad24105aae3dfec84fce81` |
| Output SHA-256 | `06ac4660b30d6de0223a26a95c8ab50433a51b4f7d8dc1c208a70b9559747661` |

The source PDF and 116-page Markdown output are not committed to the public
repository. This record verifies the routing, full-page conversion, Markdown
export, and page-traceability behavior at realistic document length.
