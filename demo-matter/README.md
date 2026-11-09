# Practice case (synthetic)

A small **made-up** case for trying Corpus Open. Nothing here is a real case, a real person or
real evidence. Do not put real papers in this folder.

## What is in it

| File | What it is |
|---|---|
| `Filings/complaint_synthetic.md` | The complaint: Demo v. Demo Corp. |
| `Filings/12-declaration_synthetic.md` | A three-page declaration, filed as Doc. 12 |
| `Evidence/evidence_memo_synthetic.md` | An evidence memo |
| `Drafts/response_draft_synthetic.md` | A draft with three planted mistakes |

The draft's planted mistakes:

1. A case that does not exist: `Harlow v. Demo Freight Lines, Inc., 812 F.4th 1099`.
2. A quotation that is not in Doc. 12 at all.
3. A real quotation from Doc. 12, cited to page 3 when it is on page 2.

The other case (`Ashcroft v. Iqbal, 556 U.S. 662`), the statute and the first quotation are real
and correct, so a check should pass them.

## Try it in Hermes

`/matter demo` copies this folder to `~/cases/corpus-open-demo`, records the three source files
with a fingerprint, and adds two deadlines and four labeled facts. The draft is left unrecorded,
so it can still be edited. Then:

- `/matter` shows the case at a glance.
- `/matter check Drafts/response_draft_synthetic.md` finds the two bad quotations. It also flags
  the made-up case once case law lookups are on (`/matter grant`).
- Ask "what's due in my case?"

## Try it from a terminal

Needs Python 3.10 or newer. Copy the folder first, so this one stays clean:

```bash
cp -R demo-matter ~/cases/practice
python3 matter.py init ~/cases/practice
python3 matter.py ingest ~/cases/practice
python3 matter.py status ~/cases/practice
python3 matter.py packet ~/cases/practice --layer 3min
```
