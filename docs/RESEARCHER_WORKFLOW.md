# Researcher workflow — 1.5.0

## What the software does for you

The workspace starts with Indexed DICOM → TXT & SQL reports → Privacy review → Export. Images and reports remain together while you compare them. Source management is under Operations.
Three independent source shares may hold DICOM, TXT reports, and a SQL dump.
The output share is a fourth, writable destination. The source shares stay read-only.

Source indexing extracts DICOM demographics and stores searchable TXT text locally.
It now also builds report evidence and ranked suggestions automatically. Rebuilding
the SQL index refreshes suggestions across both report sources. You can rerun
**Find reports for all studies** without rescanning images. Indexing comparisons
uses local SQLite evidence and indexed lookup keys, not an all-studies-by-all-reports
comparison or repeated SMB reads of every narrative. Processing uses one worker;
report evidence is ingested in pages and only ten ranked leads per study are shown.
Manual browsing remains available for the full source catalog.

## Add reports without rescanning DICOM

Wait until current jobs finish. In **Operations**, connect the report source if needed, choose its folder, and click **Add TXT / SQL from selected folders**. This imports report files while retaining the indexed DICOM catalog. For an EMR SQL export, then select the discovered dump, confirm the SQL review requirement, and click **Import reports from EMR export**. Follow Processing jobs; report suggestions refresh automatically. SQL imports require the supported dump schema. Use **Inspect export schema** if the import fails. Full source scans and forced reindexing remain under Advanced and are not required to add reports.

## Start from the review queue

Images includes a paginated, filtered queue of unassociated examinations:

- **Strong evidence**: exact accession/study identifier plus a matching full name,
  or matching full name, DOB and explicit examination date, without detected
  differences. It is a suggestion requiring confirmation, not a verified match.
- **Check details**: incomplete evidence, including surname-only leads.
- **Differences found**: inconsistent fields or multiple patient-name declarations.
  These records remain visible for investigation and manual comparison.
- **No suggestions**: no leads from the available indexed demographics. Check
  source coverage and search manually; this does not prove a report is absent.

Open **Review images and reports** to carry the examination into Reports. Read
the selected DICOM banner, inspect the images in Images, then compare suggested
reports. Each suggestion states matching, differing and missing fields. SQL comes
first when evidence is comparable; stronger TXT evidence can rank above SQL.
The researcher selects the primary report. Other source records remain available
for comparison; this release exports one primary report per study.

TXT headers such as `Patient: Synthetic Person` and `Date: 01/02/2020` are parsed.
A generic Date is displayed as a report-header date and adds supporting evidence;
it is not silently treated as the examination date. SQL result dates also remain
separate from examination dates. Matching patient IDs require checking their issuing
systems. Name normalization handles common DICOM caret and comma formats; missing,
ambiguous and unfamiliar formats require manual investigation.

Click **Compare this report** to load its independent source record. Check patient,
examination, modality and body area against DICOM, then tick the relationship
confirmation and associate. Do not infer a verified relationship from rank alone.
Association moves the study out of the unassociated queue and clears prior approval.
Prepare, check the original and sanitized report and all relevant image frames,
approve, then export to the selected output share.

## Feedback and recovery

The persistent operation banner displays the active job, phase, processed count and
elapsed time. Sources displays when matching finished and whether TXT/SQL coverage
is available. Missing SQL coverage is an error, not a zero-result lookup. Loading
states, source connection checks, job cancellation/retry, and recovery messages
distinguish work in progress from an unavailable service. Request timeouts instruct
users to check jobs before retrying because the server action may still be running.

Suggestions are hidden while rebuilding or after a scan invalidates them. Study
fingerprints prevent old evidence appearing as current after source changes. Preview
requests clear on study switching. Association verifies the preview hash again;
preparation/export validate the original report sources. Independent search remains
available when suggestions are unavailable. Imported SQL still requires the supported
`labdataex`, `labdata`, `enc`, and `items` layout; unsupported dumps fail visibly.

## Administrator setup

After upgrading, use Sources & mounts to connect TXT/bulk, DICOM imaging, SQL
database, and a separate output share. The additional SQL source slot is optional
for installations where TXT/SQL share one mount. Existing credentials/configuration
are retained. Choose folders containing all three datasets and index. Select the
dump in Sources and rebuild SQL reports; matching then refreshes automatically.
For an existing catalog, **Find reports for all studies** builds the first queue;
reindex TXT if coverage says searchable text is incomplete.

If TXT or SQL actually live in a local home directory, services cannot directly
read arbitrary home directories. The optional guided administrator import makes
protected service-readable copies without changing originals:

```bash
sudo /opt/hoag-research/venv/bin/python /opt/hoag-research/local_sources.py
```

It prompts for absolute TXT folder/SQL file paths, checks capacity, reports copy
progress, and requires `IMPORT`. Copies stay on the hospital server. Copies may
consume additional disk space; filesystem reflinks are used when supported.
After registering local sources, select the actual DICOM/local source folders and
index. Old imports are retained until an administrator handles retention/cleanup.

All development verification uses synthetic records. Validate matching coverage and
extraction on representative hospital reports locally before relying on the queue
for a research cohort; the software never automatically confirms associations or
approves de-identification.
