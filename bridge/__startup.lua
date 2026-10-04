-- REAPER MCP bridge loader
-- Lives at <REAPER resource path>/Scripts/__startup.lua, which REAPER runs at launch.
-- Its only job is to load mcp_bridge.lua and make failures visible: REAPER
-- silently swallows compile errors in startup scripts, so without this the
-- bridge can be dead with no indication anywhere.

local LOG = reaper.GetResourcePath() .. "/mcp-bridge.log"

local function log(msg)
  local f = io.open(LOG, "a")
  if f then
    f:write(os.date("%Y-%m-%d %H:%M:%S ") .. tostring(msg) .. "\n")
    f:close()
  end
end

local path = reaper.GetResourcePath() .. "/Scripts/mcp_bridge.lua"
local chunk, err = loadfile(path)

if not chunk then
  log("COMPILE ERROR in mcp_bridge.lua: " .. tostring(err))
  return
end

local ok, rerr = pcall(chunk)
if ok then
  log("bridge started")
else
  log("RUNTIME ERROR in mcp_bridge.lua: " .. tostring(rerr))
end
