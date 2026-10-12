# Report browser and reindexing (1.3.0)

The blue Reports tab opens even before selecting a study. Search all indexed TXT
and SQL reports, filter by source, sort, and page through 100 records per source.
Leave the search blank to browse all records. TXT searches use literal word/phrase
matching in the full-text index; SQL searches use case-insensitive text substrings.
An unavailable SQL service or index is shown as an error, never as zero reports.

Opening a DICOM study suggests its family name in the search field. Change or clear
that suggestion freely. Names, dates, missing demographics, and mismatches do not
exclude discovery, preview, or deliberate manual association. The report preview
describes the report's own source identity. Compare it with the selected DICOM
banner and images. Tick the relationship confirmation and click Associate report.
Association clears prior approval and does not itself authorize research export.

TXT and SQL associations both continue through Anonymize & review: prepare the
draft, check the sanitized text and all relevant images/frames, apply masks where
needed, approve, then export to the selected writable share. Source hashes are
checked again on export. A SQL index replacement invalidates old SQL references;
search and associate again. Conflicting DICOM study identities still require repair
before preparation. Source, authentication, file-size, and content-integrity checks
continue to apply. Legacy patient-filtered SQL endpoints retain their old gate;
the GUI report browser uses independent authenticated endpoints.

Sources now includes Reindex controls. Check the confirmation, then:

- **Reindex DICOM + TXT (saved folders)** rereads every file in the last successful
  scope, rebuilds TXT search, and clears study approvals. To change the scope, use
  Choose source folders instead. Completed export packages remain on the output.
- **Rebuild SQL report index** selects one active indexed `.sql` dump and queues a
  separate job. Follow line counts and completion/failure in Processing jobs.
  The root broker builds in its protected local directory, never executes dump SQL,
  and atomically replaces the index after successful parsing. Failure/cancellation
  retains the previously published index. Source shares remain read-only.

SQL rebuilding supports the validated `labdataex`, `labdata`, `enc`, and `items`
dump layout. Independent browsing includes narratives with missing relationships
and without FINDINGS/IMPRESSION headings; missing metadata remains unknown.
Other schemas and oversized/unsupported SQL statements fail visibly. Run a source
scan first so the dump appears in the selector. The installer starts `hoag-sql`;
the first SQL rebuild creates its report index. Allow local space for both the old
and replacement indexes. Nothing from source reports is sent to GitHub or AI APIs.

Update after processing jobs finish:

```bash
cd ~
git clone https://github.com/joshekey/hoag-research-pipeline.git hoag-research-pipeline-v1.3.0
cd hoag-research-pipeline-v1.3.0
sudo bash research_build --no-configure
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python /opt/hoag-research/manage.py version
sudo systemctl --no-pager status hoag-dashboard hoag-worker hoag-mount hoag-sql nginx
```

Refresh the browser with Ctrl+Shift+R. Reindex DICOM/TXT once to rebuild searchable
content, then rebuild the SQL index from Sources. Verify version `1.3.0`.
