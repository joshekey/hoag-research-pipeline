import ctypes, ctypes.util, json, random
from datetime import date
from pathlib import Path
lib=ctypes.CDLL(ctypes.util.find_library('lua5.4'))
lib.luaL_newstate.restype=ctypes.c_void_p
lib.luaL_openlibs.argtypes=[ctypes.c_void_p]
lib.luaL_loadstring.argtypes=[ctypes.c_void_p,ctypes.c_char_p]
lib.luaL_loadstring.restype=ctypes.c_int
lib.lua_pcallk.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_longlong,ctypes.c_void_p]
lib.lua_pcallk.restype=ctypes.c_int
lib.lua_tolstring.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.POINTER(ctypes.c_size_t)]
lib.lua_tolstring.restype=ctypes.c_char_p
lib.lua_close.argtypes=[ctypes.c_void_p]
source=Path(__file__).with_name('hoag_anon_study_day.lua').read_text()
harness=r'''
local script = assert(load(SOURCE))
local cache = {}
local uid_count = 0
Dcmsys={Log={storage=function() end,prefix=function() end,warn=function() end,info=function() end}}
Sequence={new=function() return '000001' end}
newuid=function() uid_count=uid_count+1; return '2.25.'..uid_count end
memcache=function(key,ttl) cache[key]=cache[key] or {}; return cache[key] end
dofile=function(path) return {['issuer|source']='P009'} end
local function run(raw,comments,study)
 local data={StudyInstanceUID=study or '1.2.3',SeriesInstanceUID='1.2.3.4',SOPInstanceUID='1.2.3.4.5',
 PatientID='source',IssuerOfPatientID='issuer',PatientAge='079Y',PatientSex='F',StudyDate=raw,
 SeriesDate='20200101',AcquisitionDate='20200102',StudyComments=comments or '',InstitutionName='Hospital'}
 dcmIn={get=function(self,k,default) if data[k]==nil then return default else return data[k] end end,
 put=function(self,k,v) data[k]=v end,remove=function(self,k) data[k]=nil end}
 script()
 return data,dcmOut
end
CASES
local data,out=run('20180809','Original clinical comment','repeat')
assert(out and data.StudyDate=='')
assert(data.StudyComments=='Original clinical comment\nHOAG_RESEARCH_STUDY_DAY=1681; BASE_DATE=2014-01-01')
assert(data.PatientID=='P009' and data.PatientName=='P009')
assert(data.PatientAge=='079Y' and data.PatientSex=='F')
assert(data.SeriesDate=='20200101' and data.AcquisitionDate=='20200102')
assert(data.InstitutionName==nil)
local uid=data.StudyInstanceUID
local data2,out2=run('20180809','','repeat')
assert(out2 and data2.StudyInstanceUID==uid)
local bad,out3=run('20180810','','repeat')
assert(out3==nil and bad.PatientID=='source' and bad.StudyDate=='20180810')
for _,raw in ipairs({'','20180229','19000229','20181301','20180431','2018-08-09','00000101'}) do
 local invalid,output=run(raw,'','invalid'..raw)
 assert(output==nil and invalid.PatientID=='source')
end
local prior,output=run('20180809','HOAG_RESEARCH_STUDY_DAY=1681','marker')
assert(output==nil and prior.PatientID=='source')
'''.replace('SOURCE',json.dumps(source))
rng=random.Random(2014)
dates=[date(2014,1,1),date(2013,12,31),date(2014,1,2),date(2018,8,9),date(2000,2,29),date(1900,3,1),date(2100,3,1)]
for _ in range(400):
 y=rng.randint(1800,2199); m=rng.randint(1,12); d=rng.randint(1,28);dates.append(date(y,m,d))
cases=[]
for i,d in enumerate(dates):
 raw=f'{d.year:04d}{d.month:02d}{d.day:02d}'; n=(d-date(2014,1,1)).days
 cases.append("do local data,out=run('%s','','case%d'); assert(out and data.StudyComments=='HOAG_RESEARCH_STUDY_DAY=%d; BASE_DATE=2014-01-01') end"%(raw,i,n))
harness=harness.replace('CASES','\n'.join(cases))
state=lib.luaL_newstate();lib.luaL_openlibs(state)
status=lib.luaL_loadstring(state,harness.encode())
if status==0:status=lib.lua_pcallk(state,0,0,0,0,None)
if status:
 error=lib.lua_tolstring(state,-1,None)
 raise RuntimeError(error.decode() if error else status)
lib.lua_close(state)
print(f'Lua validated against Python for {len(dates)} dates; output, retention, cache reuse and invalid-input hold checks passed.')
