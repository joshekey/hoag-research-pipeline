-- Hoag research cleanup for previously anonymized DICOM.
-- Set the approved signed offset before deploying. Example: -5114 maps
-- 2014-01-01 to 2000-01-01. Apply only once to each original input.
local SHIFT_DAYS = nil

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

local function parse_date(raw)
    if not raw:match("^%d%d%d%d%d%d%d%d$") then return nil end
    local year = tonumber(raw:sub(1, 4))
    local month = tonumber(raw:sub(5, 6))
    local day = tonumber(raw:sub(7, 8))
    if year < 1 or month < 1 or month > 12 then return nil end
    local lengths = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}
    if leap(year) then lengths[2] = 29 end
    if day < 1 or day > lengths[month] then return nil end
    return ordinal(year, month, day)
end

local function shift_date(raw)
    local number = parse_date(raw)
    if not number then return nil end
    number = number + SHIFT_DAYS
    if number < 1 or number > ordinal(9999, 12, 31) then return nil end
    local low, high = 1, 9999
    while low < high do
        local middle = math.floor((low + high + 1) / 2)
        if ordinal(middle, 1, 1) <= number then
            low = middle
        else
            high = middle - 1
        end
    end
    local year, month = low, 12
    while ordinal(year, month, 1) > number do
        month = month - 1
    end
    return string.format("%04d%02d%02d", year, month, number - ordinal(year, month, 1) + 1)
end

if type(SHIFT_DAYS) ~= "number" or SHIFT_DAYS ~= math.floor(SHIFT_DAYS)
    or SHIFT_DAYS == 0 or math.abs(SHIFT_DAYS) > 3652058 then
    hold("Configure a nonzero whole-day shift before running this rule")
    return
end

local updates = {}
local tags = {"StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate", "AcquisitionDateTime"}
for _, tag in ipairs(tags) do
    local raw = trim(value(tag))
    if raw ~= "" then
        local date_part = raw
        local suffix = ""
        if tag == "AcquisitionDateTime" then
            date_part = raw:sub(1, 8)
            suffix = raw:sub(9)
        end
        local shifted = shift_date(date_part)
        if not shifted then
            hold("Invalid or out-of-range date in " .. tag)
            return
        end
        updates[#updates + 1] = {tag, shifted .. suffix}
    elseif tag == "StudyDate" then
        hold("StudyDate unavailable; use the pre-conversion DICOM copy")
        return
    end
end

local ok = pcall(function()
    for _, update in ipairs(updates) do
        dcmIn:put(update[1], update[2])
        if trim(value(update[1])) ~= update[2] then
            error("DICOM date write verification failed")
        end
    end
    dcmIn:remove("InstitutionName")
    if trim(value("InstitutionName")) ~= "" then
        error("InstitutionName removal verification failed")
    end
end)
if not ok then
    hold("Date or institution update failed")
    return
end

dcmOut = dcmIn
Dcmsys.Log.info("HOAG RESEARCH calendar date shift completed")
