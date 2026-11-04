# Hoag service/study day update

Baseline: 2014-01-01 is Day 0. 2018-08-09 is Day 1681.
This is a day-count conversion, not a calendar-date shift.

## Report installation (Hoag server)

Put the package contents together in /home/sratai/hoag-study-day-update.
Run:

```bash
cd /home/sratai/hoag-study-day-update
/opt/hoag-research/venv/bin/python -m unittest -v test_hoag_study_day.py
sudo /opt/hoag-research/venv/bin/python install_study_day_update.py
```

The installer requires the currently deployed v5 engine. It makes a private
backup and changes only its sanitizer return and policy constant, plus installing
hoag_study_day.py. It keeps the existing date/phone and DOB protections.
It restarts whichever worker/dashboard services were active and restores the
previous scripts if installation fails. It does not overwrite exported reports.

Regenerate fresh drafts after successful installation:

```bash
sudo -u hoag-indexer bash <<'RUN'
set -euo pipefail
cd /opt/hoag-research
./venv/bin/python batch_patient_bundle.py --config /etc/hoag-research/config.json --prepare-latest
./venv/bin/python batch_patient_bundle.py --config /etc/hoag-research/config.json --process-latest
RUN
```

Only an explicitly labeled Date of Service, Service Date, Study Date or DOS at
the start of a line is converted. Other content, dates and timestamps remain
unchanged by this new conversion. Known redacted/invalid/ambiguous labeled dates
raise an error so the bundle holds the report for review. Reports with no such
label remain unchanged; they require inventory to determine whether a service
date needs to be supplied. Four-digit years are required. Numeric dates use US
month/day/year convention. Do not guess ambiguous two-digit years.

Validate new report hashes, DOB protections, labeled service-date conversions
and report counts before replacing shared copies. Previously shared copies and
checkpoints are not automatically updated. Release remains pending review.

## DCMSYS Lua installation

Use hoag_anon_study_day.lua on already-anonymized objects in the restricted pilot route.
It replaces the earlier full anonymization rule for this cleanup step.
It removes InstitutionName, empties StudyDate and appends a marker to StudyComments.

    HOAG_RESEARCH_STUDY_DAY=1681; BASE_DATE=2014-01-01

The script retains existing patient identifiers, UIDs, accessions and other dates.
A valid existing marker with an empty StudyDate allows retry without duplicating
the marker. Missing StudyDate without a marker holds the object. Test the
output on one representative anonymized object before processing the batch.

## Validation

Python unit tests cover baseline, negative days, leap dates, accepted formats,
targeted field conversion, timestamps, CRLF, repeated pages, idempotency and
held invalid/redacted values. Lua calendar arithmetic and output behavior are
tested locally with mocked DCMSYS APIs. These are not live DCMSYS integration
tests. The site must verify that empty DA is retained, StudyComments survives
routing, actual runtime/API behavior works and existing UID mappings persist.
