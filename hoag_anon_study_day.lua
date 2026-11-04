-- Hoag research date cleanup for previously anonymized objects.
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

local original_date = trim(value("StudyDate"))
local comments = value("StudyComments")
local marker = "HOAG_RESEARCH_STUDY_DAY="
local has_marker = comments:find(marker, 1, true)
local new_comments

if has_marker then
    local existing = comments:match("HOAG_RESEARCH_STUDY_DAY=(%-?%d+); BASE_DATE=2014%-01%-01$")
    if original_date ~= "" or not existing or comments:find(marker, has_marker + #marker, true) then
        hold("Conflicting or invalid research study-day marker")
        return
    end
elseif original_date == "" then
    hold("StudyDate unavailable; recover it from the source study")
    return
else
    local days = study_day(original_date)
    if days == nil then
        hold("Invalid StudyDate")
        return
    end
    new_comments = marker .. string.format("%.0f", days) .. "; BASE_DATE=2014-01-01"
    if trim(comments) ~= "" then
        new_comments = comments .. "\n" .. new_comments
    end
    if #new_comments > 10240 then
        hold("StudyComments length exceeds LT limit")
        return
    end
end

local ok = pcall(function()
    if new_comments then
        dcmIn:put("StudyComments", new_comments)
        dcmIn:put("StudyDate", "")
    end
    dcmIn:remove("InstitutionName")
    if trim(value("StudyDate")) ~= "" or trim(value("InstitutionName")) ~= ""
        or (new_comments and value("StudyComments") ~= new_comments) then
        error("DICOM write verification failed")
    end
end)
if not ok then
    hold("Date or institution update failed")
    return
end

dcmOut = dcmIn
Dcmsys.Log.info("HOAG RESEARCH study-day and institution cleanup completed")
