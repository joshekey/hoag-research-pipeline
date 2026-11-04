-- Hoag research days on already-anonymized DICOM.
-- Pilot: inspect saved output before enabling date clearing.
-- Requires DCMSYS private dictionary support for the keys below.
local CLEAR_SOURCE_DATES = false
local CREATOR_TAG = "0011,0011"
local CREATOR = "HOAG_RESEARCH_DAYS"
-- Register creator as LO, 1101-1105 as SL, and 1106 as LO.
-- If your DCMSYS dictionary uses keywords, substitute them below.
local fields = {
    {"StudyDate", "0011,1101"},
    {"SeriesDate", "0011,1102"},
    {"AcquisitionDate", "0011,1103"},
    {"ContentDate", "0011,1104"},
    {"AcquisitionDateTime", "0011,1105"},
}
local SUFFIX_TAG = "0011,1106"

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

local ok, reason = pcall(function()
    local creator = trim(value(CREATOR_TAG))
    if creator ~= "" then
        error("Private creator slot already occupied; use a verified free block")
    end

    local updates = {}
    local suffix = ""
    local baseline = ordinal(2014, 1, 1)
    for _, field in ipairs(fields) do
        local source, target = field[1], field[2]
        if trim(value(target)) ~= "" then error("Private field already occupied") end
        local raw = trim(value(source))
        if raw ~= "" then
            local token = raw
            if source == "AcquisitionDateTime" then
                token = raw:sub(1, 8)
                suffix = raw:sub(9)
            end
            local day = parse_date(token)
            if not day then error("Invalid date in " .. source) end
            updates[#updates + 1] = {source, target, string.format("%.0f", day - baseline)}
        elseif source == "StudyDate" then
            error("StudyDate missing; use the pre-conversion anonymized copy")
        end
    end
    if trim(value(SUFFIX_TAG)) ~= "" then error("Private suffix field already occupied") end

    local function put_checked(tag, expected)
        dcmIn:put(tag, expected)
        if trim(value(tag)) ~= expected then
            error("DCMSYS write/readback failed for " .. tag)
        end
    end

    put_checked(CREATOR_TAG, CREATOR)
    for _, update in ipairs(updates) do
        put_checked(update[2], update[3])
    end
    if suffix ~= "" then put_checked(SUFFIX_TAG, suffix) end
    if CLEAR_SOURCE_DATES then
        for _, update in ipairs(updates) do
            put_checked(update[1], "")
        end
    end
    dcmIn:remove("InstitutionName")
    if trim(value("InstitutionName")) ~= "" then
        error("InstitutionName removal did not read back")
    end
end)

if not ok then
    hold(tostring(reason))
    return
end

dcmOut = dcmIn
Dcmsys.Log.info("HOAG RESEARCH private research days verified in Lua")
