-- REAPER MCP bridge
-- Loaded by __startup.lua. Watches an IPC directory for request files,
-- executes each as ReaScript Lua, and writes back captured output, a
-- JSON-encoded return value, and any error.
--
-- File IPC with atomic renames is deliberate: REAPER's Lua has no sockets,
-- and this needs no extension, no Python-in-REAPER and no configuration.

local RES       = reaper.GetResourcePath()
local IPC       = RES .. "/mcp-bridge"
local REQ       = IPC .. "/req"
local RESP      = IPC .. "/resp"
local HEARTBEAT = IPC .. "/heartbeat"
local HB_EVERY  = 2.0

reaper.RecursiveCreateDirectory(REQ, 0)
reaper.RecursiveCreateDirectory(RESP, 0)

---------------------------------------------------------------- json encoding

local ESCAPES = {
  ['"'] = '\\"',
  ['\\'] = '\\\\',
  ['\n'] = '\\n',
  ['\r'] = '\\r',
  ['\t'] = '\\t',
  ['\b'] = '\\b',
  ['\f'] = '\\f',
}

local function esc(s)
  return (s:gsub('[%c"\\]', function(c)
    return ESCAPES[c] or string.format('\\u%04x', c:byte())
  end))
end

local function enc(v, depth)
  depth = depth or 0
  if depth > 12 then
    return '"<max depth>"'
  end
  local t = type(v)
  if v == nil then
    return 'null'
  elseif t == 'boolean' then
    return tostring(v)
  elseif t == 'number' then
    if v ~= v or v == math.huge or v == -math.huge then
      return 'null'
    end
    return string.format('%.10g', v)
  elseif t == 'string' then
    return '"' .. esc(v) .. '"'
  elseif t == 'table' then
    local n, is_array = 0, true
    for k in pairs(v) do
      n = n + 1
      if type(k) ~= 'number' then
        is_array = false
      end
    end
    if is_array and n == #v then
      local parts = {}
      for i = 1, #v do
        parts[#parts + 1] = enc(v[i], depth + 1)
      end
      return '[' .. table.concat(parts, ',') .. ']'
    end
    local parts = {}
    for k, val in pairs(v) do
      parts[#parts + 1] = '"' .. esc(tostring(k)) .. '":' .. enc(val, depth + 1)
    end
    return '{' .. table.concat(parts, ',') .. '}'
  end
  return '"<' .. t .. '>"'
end

------------------------------------------------------------------ file helpers

local function read_file(path)
  local f = io.open(path, 'rb')
  if not f then
    return nil
  end
  local data = f:read('*a')
  f:close()
  return data
end

local function write_atomic(path, data)
  local tmp = path .. '.tmp'
  local f = io.open(tmp, 'wb')
  if not f then
    return false
  end
  f:write(data)
  f:close()
  os.remove(path)
  return os.rename(tmp, path)
end

-------------------------------------------------------------------- execution

-- Returns status, result_json, output, err
local function run(code, undo_label)
  local out = {}
  local env = setmetatable({
    print = function(...)
      local parts = {}
      for i = 1, select('#', ...) do
        parts[#parts + 1] = tostring((select(i, ...)))
      end
      out[#out + 1] = table.concat(parts, '\t')
    end,
  }, { __index = _G })

  local chunk, cerr = load(code, 'mcp-request', 't', env)
  if not chunk then
    return 'error', 'null', table.concat(out, '\n'), 'compile error: ' .. tostring(cerr)
  end

  reaper.Undo_BeginBlock()
  local ok, res = pcall(chunk)
  reaper.Undo_EndBlock(undo_label ~= '' and undo_label or 'MCP', -1)

  if not ok then
    return 'error', 'null', table.concat(out, '\n'), tostring(res)
  end

  local eok, json = pcall(enc, res, 0)
  if not eok then
    json = '"<unserializable>"'
  end
  return 'ok', json, table.concat(out, '\n'), nil
end

--------------------------------------------------------------- request handling

local function parse_request(text)
  -- tolerate CRLF: a client writing the request in text mode on Windows
  -- would otherwise silently fail to match here
  local header, code = text:match('^(.-)\r?\n%-%-%-CODE%-%-%-\r?\n(.*)$')
  if not header then
    return nil, nil
  end
  local flags = {}
  for line in header:gmatch('[^\r\n]+') do
    local k, v = line:match('^([%w_]+)=(.*)$')
    if k then
      flags[k] = v
    end
  end
  return flags, code
end

local function handle(name)
  local path = REQ .. '/' .. name
  local text = read_file(path)
  os.remove(path)
  if not text then
    return
  end

  local id = name:gsub('%.req$', '')
  local flags, code = parse_request(text)

  local status, json, output, err
  if not code then
    status, json, output, err =
      'error', 'null', '', 'malformed request (missing CODE marker)'
  else
    status, json, output, err = run(code, flags.undo or '')
  end

  write_atomic(RESP .. '/' .. id .. '.resp', table.concat({
    'status=' .. status,
    '---OUTPUT---',
    output or '',
    '---RESULT---',
    json or 'null',
    '---ERROR---',
    err or '',
  }, '\n'))
end

------------------------------------------------------------------- main loop

local last_hb = 0

local function beat()
  write_atomic(HEARTBEAT, tostring(os.time()) .. '\n' .. reaper.GetAppVersion())
end

local function poll()
  local pending, i = {}, 0
  while true do
    local f = reaper.EnumerateFiles(REQ, i)
    if not f then
      break
    end
    if f:match('%.req$') then
      pending[#pending + 1] = f
    end
    i = i + 1
  end

  for _, name in ipairs(pending) do
    local ok, err = pcall(handle, name)
    if not ok then
      reaper.ShowConsoleMsg('[mcp-bridge] handler failure: ' .. tostring(err) .. '\n')
    end
  end

  local now = reaper.time_precise()
  if now - last_hb > HB_EVERY then
    last_hb = now
    beat()
  end

  reaper.defer(poll)
end

beat()
poll()
