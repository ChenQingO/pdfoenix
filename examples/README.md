# A Tiny Sample Book

This directory is a synthetic, copyright-free book. It exists so you can run the
deterministic half of the pipeline without a PDF, API key, or model call.

```bash
pdfoenix -C examples/sample_book check
pdfoenix -C examples/sample_book build --out sample.epub
```

`build/sample.epub` is a complete EPUB with a table of contents, a numbered
equation, and MathML. The sample is also useful in CI and when debugging
`check`, `build`, or `inspect`.

For a synthetic PDF with a text layer (useful when testing `init`/`transcribe`
without a real book), run:

```bash
python examples/make_sample_pdf.py /tmp/pdfoenix-sample.pdf
```
