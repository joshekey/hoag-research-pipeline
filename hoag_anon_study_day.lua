-- HOAG RESEARCH - DCMSYS RESTRICTED STAGING PILOT
-- Complete replacement for the supplied pilot Lua script.
-- Only StudyDate gets the new date treatment. Other dates are unchanged.
-- StudyDate => empty DA value (not an invalid day count)
-- StudyComments => existing comments plus HOAG_RESEARCH_STUDY_DAY=<integer>
-- Day 0 = 2014-01-01. All calculations use calendar arithmetic, not os.time.
-- This remains metadata tag replacement, NOT complete de-identification.
-- Nested tags, private data, pixels, UID references, cache persistence,
-- concurrent cache writes, and routing still require separate validation.

Dcmsys.Log.storage("anon_scripts")
Dcmsys.Log.prefix("hoag_anon")
dcmOut = nil

local function value(tag)
    return tostring(dcmIn:get(tag, "") or "")
end

local function trim(s)
    return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function hold(reason)
    Dcmsys.Log.warn("HOAG RESEARCH HOLD: " .. reason)
    dcmOut = nil
end

local function leap(year)
    return year % 4 == 0 and (year % 100 ~= 0 or year % 400 == 0)
end

local function ordinal(year, month, day)
    local before = {0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334}
    local previous = year - 1
    local result = 365 * previous + math.floor(previous / 4)
        - math.floor(previous / 100) + math.floor(previous / 400)
        + before[month] + day
    if month > 2 and leap(year) then result = result + 1 end
    return result
end

local function study_day(raw)
    if not raw:match("^%d%d%d%d%d%d%d%d$") then return nil end
    local year = tonumber(raw:sub(1, 4))
    local month = tonumber(raw:sub(5, 6))
    local day = tonumber(raw:sub(7, 8))
    if year < 1 or month < 1 or month > 12 then return nil end
    local lengths = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}
    if leap(year) then lengths[2] = 29 end
    if day < 1 or day > lengths[month] then return nil end
    return ordinal(year, month, day) - ordinal(2014, 1, 1)
end

-- Read and validate everything before modifying dcmIn or publishing dcmOut.
local study_uid = trim(value("StudyInstanceUID"))
local series_uid = trim(value("SeriesInstanceUID"))
local sop_uid = trim(value("SOPInstanceUID"))
local original_id = trim(value("PatientID"))
local original_issuer = trim(value("IssuerOfPatientID"))
local original_age = trim(value("PatientAge"))
local original_date = trim(value("StudyDate"))
local original_comments = value("StudyComments")

if study_uid == "" or series_uid == "" or sop_uid == "" then
    hold("Missing required DICOM UID")
    return
end
if original_id == "" then
    hold("Missing PatientID")
    return
end

local days = study_day(original_date)
if days == nil then
    hold("Missing or invalid StudyDate; research day cannot be calculated")
    return
end
if original_comments:find("HOAG_RESEARCH_STUDY_DAY=", 1, true) then
    hold("Study-day marker already present; use original source input")
    return
end

local day_marker = "HOAG_RESEARCH_STUDY_DAY=" .. string.format("%.0f", days)
    .. "; BASE_DATE=2014-01-01"
local new_comments = day_marker
if trim(original_comments) ~= "" then
    new_comments = original_comments .. "\n" .. day_marker
end
-- StudyComments is LT: reject overflow rather than silently truncating.
if #new_comments > 10240 then
    hold("StudyComments length exceeds LT limit")
    return
end

local crosswalk_path = "/var/lib/dcmsys/hoag-research/hoag_patient_map.lua"
local ok, patient_map = pcall(dofile, crosswalk_path)
if not ok or type(patient_map) ~= "table" then
    hold("Verified crosswalk unavailable or invalid")
    return
end
local identity_key = original_issuer .. "|" .. original_id
local anon_id = patient_map[identity_key]
if type(anon_id) ~= "string" or not anon_id:match("^P%d%d%d$") then
    hold("Patient identifier not found in verified crosswalk")
    return
end

-- Original 30-day pilot mapping cache is retained.
local ttl = 60 * 60 * 24 * 30
local study_cache = memcache("HOAG_RESEARCH_STUDY:" .. study_uid, ttl)
local existing_patient = study_cache["patient_label"]
if existing_patient ~= nil and existing_patient ~= anon_id then
    hold("Conflicting patient identities within study")
    return
end
-- New binding prevents different dates within the same study on future input.
local existing_date = study_cache["study_day_source_date_v1"]
if existing_date ~= nil and existing_date ~= original_date then
    hold("Conflicting StudyDate values within study")
    return
end
study_cache["patient_label"] = anon_id
study_cache["study_day_source_date_v1"] = original_date

local new_study_uid = study_cache["new_study_uid"]
if new_study_uid == nil then
    new_study_uid = newuid()
    study_cache["new_study_uid"] = new_study_uid
end
local new_accession = study_cache["new_accession"]
if new_accession == nil then
    new_accession = "H" .. tostring(Sequence.new("AccessionNumber", 6, 1))
    study_cache["new_accession"] = new_accession
end
local series_key = "SERIES:" .. series_uid
local new_series_uid = study_cache[series_key]
if new_series_uid == nil then
    new_series_uid = newuid()
    study_cache[series_key] = new_series_uid
end
local sop_key = "SOP:" .. sop_uid
local new_sop_uid = study_cache[sop_key]
if new_sop_uid == nil then
    new_sop_uid = newuid()
    study_cache[sop_key] = new_sop_uid
end

-- Apply values while output stays nil until all writes succeed.
local output = dcmIn
local success = pcall(function()
    output:put("PatientName", anon_id)
    output:put("PatientID", anon_id)
    output:put("AccessionNumber", new_accession)
    output:put("StudyInstanceUID", new_study_uid)
    output:put("SeriesInstanceUID", new_series_uid)
    output:put("SOPInstanceUID", new_sop_uid)
    output:put("StudyDate", "")
    output:put("StudyComments", new_comments)

    -- Same identifying metadata removals as the supplied pilot.
    for _, tag in ipairs({
        "PatientBirthDate", "InstitutionName", "IssuerOfPatientID",
        "OperatorsName", "RequestingPhysician", "ReferringPhysicianName",
        "InstitutionAddress", "StationName"
    }) do
        output:remove(tag)
    end
    if original_age ~= "" then output:put("PatientAge", original_age) end
end)
if not success then
    hold("DICOM tag update failed")
    return
end

dcmOut = output
Dcmsys.Log.info("HOAG RESEARCH study-day pilot completed for " .. anon_id)
