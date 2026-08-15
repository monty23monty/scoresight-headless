# OCR acceptance corpus

Place real scoreboard frames in this directory without committing sensitive venue footage unless
you have permission. Create a `manifest.jsonl` file in capture order. Each line uses region IDs or
names from the production configuration:

```json
{"image":"frames/000001.png","expected":{"clock":"12:34","home":"7","away":"12"}}
```

Evaluate the corpus in the Linux OCR environment:

```bash
python scripts/evaluate_ocr_corpus.py acceptance/corpus/manifest.jsonl \
  /var/lib/scoresight/config-v1.json --tessdata tesseract/tessdata
```

The default gate requires 200 frames, 99% accepted precision, no more than one false accepted
update, and p95 processing latency no greater than 750 ms.
