// frida agent for Minecraft Bedrock 1.26.45.01 (Windows x64).
//
// Load: frida -p <pid> -l agent/agent.js
// 或由 driver/session.py 注入。
//
// 设计约束：
//   - 只读观测走内存；写操作只经游戏自己的数据通路（组件 / 输入解析器），不合成键鼠。
//   - 所有地址都是 1.26.45.01 的 VA（image base 0x140000000）。启动时用字节指纹校验，
//     指纹不符就禁用对应 hook，避免在别的版本上写坏内存。

'use strict';

const IMAGE_BASE = 0x140000000;

const VA = {
  enttStorageGet: 0x141953860, // (registry, typeId) -> storage
  enttEmplace: 0x1401FC7B0, // (storage, outPair[2], entityPtr, force) -> outPair
  localPlayerPostMoveTick: 0x1477705B0, // LocalPlayerPostMoveSystem_Tick，每 tick 一次
  moveInputResolve: 0x145A5F910, // MoveInput_ResolveDirectionAndSpeedFactor
  moveInputPipeline: 0x1465D5A50, // MoveInput_RawToResolvedPipeline（仅用于诊断计数）
  blockQuery: 0x144767E30, // 方块 AABB 查询包装：可捕获 BlockSource 指针
};

// 1.26.45.01 函数入口指纹（前 24 字节）。换版本时先更新这里。
const SIG = {
  enttStorageGet: '55 56 57 53 48 83 EC 78 48 8D 6C 24 70 48 C7 45 00 FE FF FF FF 48 89 CE',
  enttEmplace: '41 57 41 56 41 55 41 54 56 57 55 53 48 83 EC 48 44 88 4C 24 3F 4C 89 C3',
  localPlayerPostMoveTick: '55 41 57 41 56 41 55 41 54 56 57 53 48 81 EC 28 04 00 00 48 8D AC 24 80',
  moveInputResolve: '48 89 C8 F3 0F 10 52 04 F3 0F 10 5A 08 0F 57 C0 0F 2E D0 0F 85 A1 00 00',
  moveInputPipeline: '55 41 57 41 56 41 55 41 54 56 57 53 48 81 EC 78 04 00 00 48 8D AC 24 80',
  blockQuery: '55 41 57 41 56 41 55 41 54 56 57 53 48 81 EC 98 01 00 00 48 8D AC 24 80',
};

// 组件 id = FNV-1a-32(组件名)，已用 IDB 里的 id 立即数与 FNV 复算双向核对。
const CID = {
  stateVector: 0x1b5d5238,
  moveInput: 0x018b1887,
  rawMoveInput: 0x3613e513,
  playerInputRequest: 0x6b04a058,
  actorRotation: 0x75df36b7,
  actorDataFlag: 0xc67426f3,
  onGroundFlag: 0xc29078a0,
  localPlayer: 0xacb47b38,
  player: 0xf95d258f,
  jumpFromGroundRequest: 0x4ea8c276,
  localPlayerJumpRequest: 0xda6293f1,
  triggerJumpRequest: 0xf37efb0f,
  actorSetPositionRequest: 0x5e3ca211,
  movementAbilities: 0x6de78dc5,
};

const STRIDE = {
  stateVector: 36,
  moveInput: 100,
  actorRotation: 16,
  actorDataFlag: 24,
  actorSetPositionRequest: 12,
  movementAbilities: 16,
};

// sparse_set 相对 storage 指针的偏移（R140 布局，元素寻址由 R121/R140 互证）。
const SS = {
  sparseTable: 0x08, // 页指针表 begin（end 在 +0x10）
  packedBegin: 0x20, // packed 实体表 begin（end 在 +0x28）
  packedEnd: 0x28,
  chunkTable: 0x50, // 载荷 chunk 指针表 begin（end 在 +0x58）
};

// StateVector 偏移。注意 +0x04 的 y 是**眼睛位置**（feet + EYE_HEIGHT），
// HUD / 命令里的 y 是脚部；对外的 getState/tp 统一用脚部坐标。
const EYE_HEIGHT = 1.6200101;
const SV = { pos: 0x00, vel: 0x18 };
// ActorRotation：{pitch, yaw, prevPitch, prevYaw}
const ROT = { pitch: 0x00, yaw: 0x04, prevPitch: 0x08, prevYaw: 0x0c };
// MoveInput：+0x24 = 解析后 strafe(local x)，+0x28 = 解析后 forward(local z)；
// +0x60 = 第二组标志位。轴序由 AutoJumpSweepCheck 的 yaw 旋转公式反推。
const MI = { resolvedX: 0x24, resolvedY: 0x28, flags16: 0x60 };

let base = null;
let moduleName = null;
let moduleEnd = null;

const state = {
  signature: {}, // name -> {va, ok, actual, expected}
  hooks: {}, // name -> listener
  emplaceFn: null,
  registry: null,
  registryCandidates: new Map(), // ptr string -> NativePointer
  registryFrom: null, // 最近一次捕获时的 type id
  storageCache: new Map(), // `${registry}:${typeId}` -> NativePointer
  player: null, // 实体 id (u32 incl. version bits)
  playerRefreshedAtTick: -1,
  playerMoveInput: null, // NativePointer
  serverMoveInput: null, // 服务端 registry 里同一玩家的 MoveInput
  serverRot: null, // 服务端 registry 里的 ActorRotation
  serverAdf: null, // 服务端 registry 里的 ActorDataFlag（sneak/sprint 状态位）
  serverAbilities: null, // 服务端 registry 里的 MovementAbilities
  serverEntityId: null, // 服务端 registry 里同一玩家的实体 id
  includeRaw: false, // 采样时是否附带组件原始字节（标定用）
  inputPassthrough: false, // true = 不写输入，让真实按键透传（标定用）
  dumpCid: null, // 标定用：额外 dump 这个组件 id 的 storage chunk
  dumpLen: 2048,
  moveMode: null, // 'sprint' / 'sneak'（标定得到的语义状态，见 MOVE_FLAGS）
  moveFlagsApplied: 0, // 语义状态写入次数
  stateFlagsApplied: 0, // 状态位写入次数
  adfOverride: null, // 实验用 ADF 位覆盖（word/bit/mask）
  rawInput: null, // 实验用：连原始输入字段一起写
  playerSync: null,
  playerRot: null,
  playerAdf: null,
  playerOnGround: null,
  tickCount: 0,
  getterCalls: 0,
  pipelineCalls: 0,
  resolverCalls: 0,
  serverResolverCalls: 0,
  blockQueryCalls: 0,
  blockQueryCandidates: new Map(),
  blockAccessor: null,
  blockHolder: null,
  blockAccessorVtable: null,
  lastBlock: null,
  lastBlockQuery: null,
  lastBlockQueryRet: null,
  lastError: null,
  inWorld: false,
  input: { x: 0, y: 0, jump: false, sneak: false, sprint: false },
  look: null, // {yaw, pitch} 或 null；每 tick 钉住，抵抗相机系统回写
  inputAppliedTicks: 0,
  pendingTp: null,
  pendingServerTp: null,
  lastTp: null,
  jumpOnce: false,
  lastJump: null,
  recording: { active: false, max: 0, samples: [], dropped: 0, startedTick: 0 },
};

const log = (level, message, extra) => {
  try {
    send({ type: 'log', level, message, extra: extra || null });
  } catch (e) {
    // send 在卸载阶段可能失败，忽略
  }
};

function resolveBase() {
  if (base !== null) return base;
  const modules = Process.enumerateModules();
  const mod =
    modules.find((m) => m.name.toLowerCase() === 'minecraft.windows.exe') ||
    modules.find((m) => m.name.toLowerCase().indexOf('minecraft') >= 0);
  if (!mod) throw new Error('Minecraft.Windows.exe 未找到');
  moduleName = mod.name;
  base = mod.base;
  moduleEnd = mod.base.add(mod.size);
  return base;
}

function addr(va) {
  return resolveBase().add(va - IMAGE_BASE);
}

function hex(n) {
  if (n === null || n === undefined) return null;
  return '0x' + n.toString(16);
}

function parseSig(s) {
  return s.trim().split(/\s+/).map((x) => parseInt(x, 16));
}

function verifySignatures() {
  for (const [name, expectedHex] of Object.entries(SIG)) {
    const expected = parseSig(expectedHex);
    const target = addr(VA[name]);
    let actual = null;
    let ok = false;
    try {
      const bytes = new Uint8Array(target.readByteArray(expected.length));
      actual = Array.from(bytes);
      ok = expected.every((b, i) => b === actual[i]);
    } catch (e) {
      actual = [];
      ok = false;
    }
    state.signature[name] = {
      va: hex(VA[name]),
      address: target.toString(),
      ok,
      expected: expectedHex,
      actual: actual.map((b) => b.toString(16).padStart(2, '0')).join(' '),
    };
    if (!ok) log('error', `signature mismatch: ${name} @ ${hex(VA[name])}`, actual);
  }
  return Object.values(state.signature).every((s) => s.ok);
}

// ---------------------------------------------------------------------------
// ECS 访问
// ---------------------------------------------------------------------------

// registry 哈希表：bucket@registry+0x48, count=(end-begin)/8, 节点池@+0x68，
// 节点 = {next@0, typeId@8, storage@16}。纯内存遍历，不调用游戏函数。
function findStorage(registry, typeId) {
  if (!registry || registry.isNull()) return null;
  const cacheKey = `${registry}:${typeId >>> 0}`;
  if (state.storageCache.has(cacheKey)) {
    const cached = state.storageCache.get(cacheKey);
    if (cached && !cached.isNull()) return cached;
  }

  const begin = registry.add(0x48).readPointer();
  const end = registry.add(0x50).readPointer();
  if (begin.isNull() || end.isNull()) return null;
  const bucketCount = end.sub(begin).toInt32() / 8;
  if (bucketCount <= 0 || (bucketCount & (bucketCount - 1)) !== 0) return null;

  const pool = registry.add(0x68).readPointer();
  const poolEnd = registry.add(0x70).readPointer();
  if (pool.isNull()) return null;

  let bucket = begin.add(((typeId >>> 0) & (bucketCount - 1)) * 8);
  for (let guard = 0; guard < 64; guard++) {
    const nodeIndex = bucket.readU32();
    if (nodeIndex === 0xffffffff) return null;
    const node = pool.add(nodeIndex * 32);
    if (node.add(8).readU32() === (typeId >>> 0)) {
      if (node.equals(poolEnd)) return null;
      const storage = node.add(16).readPointer();
      if (storage.isNull()) return null;
      // storage 指向 sparse_set 子对象，头 8 字节是 sparse_set vtable。
      try {
        const vt = storage.readPointer();
        // 只要求 vtable 落在游戏模块内。不同组件族由不同的建存储函数创建，
        // vtable 可能是 off_14E7B7F10 / off_14E762A60 / 其他专用表。
        const vtOk = vt.compare(base) >= 0 && vt.compare(moduleEnd) < 0;
        if (!vtOk) {
          log('warn', `storage vtable out of module for ${hex(typeId)}`, {
            storage: storage.toString(),
            vtable: vt.toString(),
          });
          return null;
        }
      } catch (e) {
        return null;
      }
      state.storageCache.set(cacheKey, storage);
      return storage;
    }
    bucket = node;
  }
  return null;
}

function packedEntities(storage) {
  if (!storage) return [];
  const begin = storage.add(SS.packedBegin).readPointer();
  const end = storage.add(SS.packedEnd).readPointer();
  if (begin.isNull() || end.isNull()) return [];
  const count = end.sub(begin).toInt32() / 4;
  if (count <= 0 || count > 100000) return [];
  const out = new Array(count);
  for (let i = 0; i < count; i++) out[i] = begin.add(i * 4).readU32();
  return out;
}

function denseIndexOf(storage, entity) {
  const sparseBegin = storage.add(SS.sparseTable).readPointer();
  if (sparseBegin.isNull()) return -1;
  const pageOffset = (entity & 0x3f800) >>> 8;
  let page;
  try {
    page = sparseBegin.add(pageOffset).readPointer();
  } catch (e) {
    return -1;
  }
  if (page.isNull()) return -1;
  const entry = page.add(4 * (entity & 0x7ff)).readU32();
  if (((entry ^ (entity & 0xfffc0000)) >>> 0) > 0x3fffe) return -1;
  return entry & 0x3ffff;
}

function hasComponent(typeId) {
  const storage = storageFor(typeId);
  if (!storage || state.player === null) return false;
  try {
    return denseIndexOf(storage, state.player) >= 0;
  } catch (e) {
    return false;
  }
}

function componentAddr(storage, entity, stride) {
  if (!storage) return null;
  const dense = denseIndexOf(storage, entity);
  if (dense < 0) return null;
  const chunkTable = storage.add(SS.chunkTable).readPointer();
  if (chunkTable.isNull()) return null;
  const chunk = chunkTable.add(8 * (dense >> 7)).readPointer();
  if (chunk.isNull()) return null;
  return chunk.add(stride * (dense & 0x7f));
}

function storageFor(typeId) {
  if (!state.registry) return null;
  return findStorage(state.registry, typeId);
}

function component(typeId, stride) {
  const storage = storageFor(typeId);
  if (!storage || state.player === null) return null;
  return componentAddr(storage, state.player, stride);
}

// 本地世界的服务端 registry：没有 LocalPlayerComponent，但有 PlayerComponent。
function findServerContext() {
  for (const [key, reg] of state.registryCandidates) {
    if (state.registry && reg.equals(state.registry)) continue;
    const storage = findStorage(reg, CID.player);
    if (!storage) continue;
    const entities = packedEntities(storage);
    if (entities.length === 0) continue;
    return { registry: reg, entity: entities[0] >>> 0, key };
  }
  return null;
}

function ensureComponentIn(registry, entity, typeId, stride) {
  const storage = findStorage(registry, typeId);
  if (!storage) return null;
  let elem = componentAddr(storage, entity, stride);
  if (elem) return elem;
  if (!state.emplaceFn) return null;
  try {
    const out = Memory.alloc(16);
    const ent = Memory.alloc(4);
    ent.writeU32(entity >>> 0);
    state.emplaceFn(storage, out, ent, 0);
    return componentAddr(storage, entity, stride);
  } catch (e) {
    log('warn', `server emplace failed for ${hex(typeId)}`, String(e));
    return null;
  }
}

// 服务端权威传送：在服务端 registry 的玩家实体上排队 ActorSetPositionRequest。
// 实测有效：下一 tick 服务端应用后同步回客户端。
function serverTeleport(x, y, z) {
  const pos = [Number(x), Number(y), Number(z)];
  if (!pos.every(Number.isFinite)) return { ok: false, error: 'position must be finite' };
  const ctx = findServerContext();
  if (!ctx) return { ok: false, error: 'server registry not found' };
  const req = ensureComponentIn(
    ctx.registry,
    ctx.entity,
    CID.actorSetPositionRequest,
    STRIDE.actorSetPositionRequest,
  );
  if (!req) return { ok: false, error: 'server ActorSetPositionRequestComponent unavailable' };
  writeVec3(req, [pos[0], pos[1] + EYE_HEIGHT, pos[2]]);
  return {
    ok: true,
    via: 'server',
    registry: ctx.registry.toString(),
    entity: hex(ctx.entity),
    ptr: req.toString(),
    pos,
  };
}

// 服务端与客户端各自持有一份玩家 MoveInput/ActorRotation。只写客户端时，
// 服务端会按零输入校正，表现为位置来回拉扯。这里同时缓存服务端元素地址。
function refreshServerPointers() {
  const ctx = findServerContext();
  if (!ctx) {
    state.serverMoveInput = null;
    state.serverRot = null;
    state.serverAdf = null;
    state.serverAbilities = null;
    state.serverEntityId = null;
    return;
  }
  state.serverEntityId = ctx.entity >>> 0;
  state.serverMoveInput = componentAddr(
    findStorage(ctx.registry, CID.moveInput),
    ctx.entity,
    STRIDE.moveInput,
  );
  state.serverRot = componentAddr(
    findStorage(ctx.registry, CID.actorRotation),
    ctx.entity,
    STRIDE.actorRotation,
  );
  state.serverAdf = componentAddr(
    findStorage(ctx.registry, CID.actorDataFlag),
    ctx.entity,
    STRIDE.actorDataFlag,
  );
  state.serverAbilities = componentAddr(
    findStorage(ctx.registry, CID.movementAbilities),
    ctx.entity,
    STRIDE.movementAbilities,
  );
}

// 组件原始字节：标定用（对比"真实按键"与"注入"时内存里到底哪些字节不同）。
function hexBytes(ptr, len) {
  if (!ptr) return null;
  try {
    const parts = [];
    for (let i = 0; i < len; i += 1) {
      parts.push(('0' + ptr.add(i).readU8().toString(16)).slice(-2));
    }
    return parts.join('');
  } catch (e) {
    state.lastError = String(e);
    return null;
  }
}

function readRawComponents() {
  refreshServerPointers();
  if (state.player === null) return null;
  refreshPlayer(false);
  const clientAbilities = component(CID.movementAbilities, STRIDE.movementAbilities);
  const out = {
    client: {
      entity: state.player >>> 0,
      move_input: hexBytes(state.playerMoveInput, STRIDE.moveInput),
      adf: hexBytes(state.playerAdf, STRIDE.actorDataFlag),
      abilities: hexBytes(clientAbilities, STRIDE.movementAbilities),
    },
    server: {
      entity: state.serverEntityId,
      move_input: hexBytes(state.serverMoveInput, STRIDE.moveInput),
      adf: hexBytes(state.serverAdf, STRIDE.actorDataFlag),
      abilities: hexBytes(state.serverAbilities, STRIDE.movementAbilities),
    },
  };
  // 实验用：整块 dump 某个组件 storage 的 chunk（stride 未知时也能拿到字节）。
  if (state.dumpCid !== null) {
    out.storage = {
      cid: `0x${(state.dumpCid >>> 0).toString(16)}`,
      hex: storageChunkBytes(state.dumpCid, state.dumpLen),
    };
  }
  return out;
}

// 直接把玩家所在 chunk 的前 length 字节读出来：不依赖 stride，
// 用于"不知道组件布局、但想知道真实按键改了哪些字节"的标定。
function storageChunkBytes(typeId, length) {
  try {
    const storage = storageFor(typeId);
    if (!storage || state.player === null) return null;
    const dense = denseIndexOf(storage, state.player);
    if (dense < 0) return null;
    const chunkTable = storage.add(SS.chunkTable).readPointer();
    if (chunkTable.isNull()) return null;
    const chunk = chunkTable.add(8 * (dense >> 7)).readPointer();
    if (chunk.isNull()) return null;
    return hexBytes(chunk, length);
  } catch (e) {
    state.lastError = String(e);
    return null;
  }
}

// 读/写前取最新元素地址；失败时强制刷新玩家（重生/换维度后实体会变）。
function freshComponent(typeId, stride) {
  let ptr = component(typeId, stride);
  if (ptr) return ptr;
  if (refreshPlayer(true) === null) return null;
  return component(typeId, stride);
}

function readVec3(p) {
  return [p.readFloat(), p.add(4).readFloat(), p.add(8).readFloat()];
}

function writeVec3(p, v) {
  p.writeFloat(v[0]);
  p.add(4).writeFloat(v[1]);
  p.add(8).writeFloat(v[2]);
}

function isFiniteVec3(v) {
  return v.every((x) => Number.isFinite(x) && Math.abs(x) < 3.0e7);
}

// 读 MSVC std::string：{buf/ptr(16), size(8), capacity(8)}；size<=15 时数据内联。
function readStdString(base, offset) {
  try {
    const field = base.add(offset);
    const size = field.add(0x10).readU64().toNumber();
    if (!(size > 0 && size <= 512)) return null;
    const dataPtr = size <= 15 ? field : field.readPointer();
    if (dataPtr.isNull()) return null;
    const bytes = new Uint8Array(dataPtr.readByteArray(size));
    let out = '';
    for (let i = 0; i < bytes.length; i++) out += String.fromCharCode(bytes[i]);
    return out;
  } catch (e) {
    return null;
  }
}

// 从 registry 候选里挑出“有且至少一个 LocalPlayer”的那个。
function resolveRegistry() {
  if (state.registry && state.player !== null) return state.registry;
  for (const [key, reg] of state.registryCandidates) {
    try {
      const lp = findStorage(reg, CID.localPlayer);
      if (!lp) continue;
      const entities = packedEntities(lp);
      if (entities.length === 0) continue;
      state.registry = reg;
      state.registryFrom = 'LocalPlayerComponent';
      log('info', `registry resolved from ${key}`, {
        registry: reg.toString(),
        localPlayers: entities.length,
      });
      return reg;
    } catch (e) {
      // 候选无效，试下一个
    }
  }
  return null;
}

function refreshPlayer(force) {
  if (!resolveRegistry()) return null;
  if (
    !force &&
    state.player !== null &&
    state.playerRefreshedAtTick >= 0 &&
    state.tickCount - state.playerRefreshedAtTick < 100
  ) {
    return state.player;
  }

  const svStorage = storageFor(CID.stateVector);
  const previousPlayer = state.player;
  const tryTags = [CID.localPlayer, CID.player];
  for (const tag of tryTags) {
    const storage = storageFor(tag);
    if (!storage) continue;
    const entities = packedEntities(storage);
    for (const entity of entities) {
      const sv = componentAddr(svStorage, entity, STRIDE.stateVector);
      if (!sv) continue;
      try {
        if (!isFiniteVec3(readVec3(sv))) continue;
      } catch (e) {
        continue;
      }
      state.player = entity >>> 0;
      state.playerRefreshedAtTick = state.tickCount;
      state.playerSync = sv;
      state.playerRot = componentAddr(storageFor(CID.actorRotation), entity, STRIDE.actorRotation);
      state.playerAdf = componentAddr(storageFor(CID.actorDataFlag), entity, STRIDE.actorDataFlag);
      state.playerOnGround = componentAddr(storageFor(CID.onGroundFlag), entity, 1);
      state.playerMoveInput = componentAddr(storageFor(CID.moveInput), entity, STRIDE.moveInput);
      state.inWorld = true;
      if (previousPlayer !== state.player) {
        log('info', 'local player resolved', {
          entity: hex(state.player),
          via: hex(tag),
          stateVector: state.playerSync ? state.playerSync.toString() : null,
          moveInput: state.playerMoveInput ? state.playerMoveInput.toString() : null,
        });
      }
      return state.player;
    }
  }
  state.player = null;
  state.inWorld = false;
  state.registry = null;
  state.storageCache.clear();
  return null;
}

function readState() {
  try {
    const player = refreshPlayer(false);
    if (player === null) {
      return { ok: false, error: 'local player not resolved (not in world?)' };
    }
    const sv = freshComponent(CID.stateVector, STRIDE.stateVector);
    if (!sv) return { ok: false, error: 'StateVectorComponent missing' };
    state.playerSync = sv;
    const eyePos = readVec3(sv.add(SV.pos));
    const pos = [eyePos[0], eyePos[1] - EYE_HEIGHT, eyePos[2]];
    const vel = readVec3(sv.add(SV.vel));

    let yaw = null;
    let pitch = null;
    const rot = freshComponent(CID.actorRotation, STRIDE.actorRotation);
    if (rot) {
      state.playerRot = rot;
      pitch = rot.add(ROT.pitch).readFloat();
      yaw = rot.add(ROT.yaw).readFloat();
    }

    let flagsLo = null;
    let flagsMid = null;
    let flagsHi = null;
    const adf = freshComponent(CID.actorDataFlag, STRIDE.actorDataFlag);
    if (adf) {
      state.playerAdf = adf;
      flagsLo = adf.readU64().toString();
      flagsMid = adf.add(8).readU64().toString();
      flagsHi = adf.add(16).readU64().toString();
    }

    const onGround = hasComponent(CID.onGroundFlag);

    let input = null;
    const mi = freshComponent(CID.moveInput, STRIDE.moveInput);
    if (mi) {
      state.playerMoveInput = mi;
      input = {
        x: mi.add(MI.resolvedX).readFloat(),
        y: mi.add(MI.resolvedY).readFloat(),
        flags: mi.add(MI.flags16).readU16(),
      };
    }

    return {
      ok: true,
      tick: state.tickCount,
      entity: player,
      pos,
      eye_pos: eyePos,
      vel,
      yaw,
      pitch,
      on_ground: onGround,
      flags_lo: flagsLo,
      flags_mid: flagsMid,
      flags_hi: flagsHi,
      input,
    };
  } catch (e) {
    state.lastError = String(e);
    return { ok: false, error: String(e) };
  }
}

function snapshotSample() {
  const s = readState();
  if (!s.ok) return { t: state.tickCount, ok: false, error: s.error };
  const sample = {
    t: s.tick,
    ok: true,
    pos: s.pos,
    eye_pos: s.eye_pos,
    vel: s.vel,
    yaw: s.yaw,
    pitch: s.pitch,
    on_ground: s.on_ground,
    flags_lo: s.flags_lo,
    flags_mid: s.flags_mid,
    flags_hi: s.flags_hi,
    input: s.input,
  };
  if (state.includeRaw) sample.raw = readRawComponents();
  return sample;
}

// ---------------------------------------------------------------------------
// 写路径
// ---------------------------------------------------------------------------

function ensureComponent(typeId, stride) {
  const storage = storageFor(typeId);
  if (!storage) return null;
  let elem = componentAddr(storage, state.player, stride);
  if (elem) return elem;
  if (!state.emplaceFn) return null;
  try {
    const out = Memory.alloc(16);
    const ent = Memory.alloc(4);
    ent.writeU32(state.player >>> 0);
    state.emplaceFn(storage, out, ent, 0);
    elem = componentAddr(storage, state.player, stride);
    return elem;
  } catch (e) {
    log('warn', `emplace failed for ${hex(typeId)}`, String(e));
    return null;
  }
}

function ensurePresence(typeId) {
  const storage = storageFor(typeId);
  if (!storage) return false;
  if (denseIndexOf(storage, state.player) >= 0) return true;
  if (!state.emplaceFn) return false;
  try {
    const out = Memory.alloc(16);
    const ent = Memory.alloc(4);
    ent.writeU32(state.player >>> 0);
    state.emplaceFn(storage, out, ent, 0);
    return denseIndexOf(storage, state.player) >= 0;
  } catch (e) {
    log('warn', `emplace presence failed for ${hex(typeId)}`, String(e));
    return false;
  }
}

function applyPendingTp() {
  const tp = state.pendingTp;
  if (!tp) return;
  state.pendingTp = null;
  try {
    const direct = () => {
      const sv = freshComponent(CID.stateVector, STRIDE.stateVector);
      if (!sv) throw new Error('StateVectorComponent missing');
      state.playerSync = sv;
      const eyePos = [tp.pos[0], tp.pos[1] + EYE_HEIGHT, tp.pos[2]];
      writeVec3(sv.add(SV.pos), eyePos);
      sv.add(SV.vel).writeFloat(0);
      sv.add(SV.vel + 4).writeFloat(0);
      sv.add(SV.vel + 8).writeFloat(0);
    };
    if (tp.mode === 'direct') {
      direct();
      state.lastTp = { ok: true, mode: 'direct', pos: tp.pos, tick: state.tickCount };
      return;
    }
    const req = ensureComponent(CID.actorSetPositionRequest, STRIDE.actorSetPositionRequest);
    if (req) {
      writeVec3(req, [tp.pos[0], tp.pos[1] + EYE_HEIGHT, tp.pos[2]]);
      state.lastTp = { ok: true, mode: 'request', pos: tp.pos, tick: state.tickCount };
    } else {
      direct();
      state.lastTp = { ok: true, mode: 'direct-fallback', pos: tp.pos, tick: state.tickCount };
    }
  } catch (e) {
    state.lastTp = { ok: false, error: String(e), tick: state.tickCount };
  }
}

function doJumpRequest() {
  // JumpFromGroundSystem 每 tick 遍历带 JumpFromGroundRequestComponent 的实体；
  // 把该组件 emplac 到玩家身上等价于“按一次跳”。组件由系统在消费后移除。
  // tag 组件可能没有载荷 chunk，因此成功判据是“稀疏集里存在”，不是元素指针非空。
  const present = ensurePresence(CID.jumpFromGroundRequest);
  if (present) {
    state.lastJump = { ok: true, tick: state.tickCount };
  } else if (!state.lastJump || !state.lastJump.ok) {
    state.lastJump = {
      ok: false,
      tick: state.tickCount,
      error: 'JumpFromGroundRequestComponent unavailable',
    };
  }
}

function applyLookOverride() {
  if (!state.look) return;
  const { yaw, pitch } = state.look;
  const rot = freshComponent(CID.actorRotation, STRIDE.actorRotation);
  if (rot) {
    rot.add(ROT.pitch).writeFloat(pitch);
    rot.add(ROT.yaw).writeFloat(yaw);
    rot.add(ROT.prevPitch).writeFloat(pitch);
    rot.add(ROT.prevYaw).writeFloat(yaw);
  }
  const srot = state.serverRot;
  if (srot) {
    srot.add(ROT.pitch).writeFloat(pitch);
    srot.add(ROT.yaw).writeFloat(yaw);
    srot.add(ROT.prevPitch).writeFloat(pitch);
    srot.add(ROT.prevYaw).writeFloat(yaw);
  }
}

function applyInputOverride() {
  // 标定用透传：让玩家的**真实按键**原样进入游戏。
  // 默认不是透传——agent 每 tick 都会把 state.input 写进两份 MoveInput，
  // 而 state.input 默认是 0，所以挂着 agent 时真实按键会被零输入覆盖掉。
  if (state.inputPassthrough) return;
  const inp = state.input;
  const mi = freshComponent(CID.moveInput, STRIDE.moveInput);
  if (mi) {
    state.playerMoveInput = mi;
    mi.add(MI.resolvedX).writeFloat(inp.x);
    mi.add(MI.resolvedY).writeFloat(inp.y);
    if (state.rawInput) writeRawInput(mi, inp);
  }
  const smi = state.serverMoveInput;
  if (smi) {
    smi.add(MI.resolvedX).writeFloat(inp.x);
    smi.add(MI.resolvedY).writeFloat(inp.y);
    if (state.rawInput) writeRawInput(smi, inp);
  }
  state.inputAppliedTicks++;
}

// 实验用：连同"原始输入"字段（+0x00 标志、+0x04/+0x08 原始轴）一起写。
// 解析器只读 +0x24/+0x28，但输入源标志位可能在 +0x00 的 bit0（见 docs §4）。
function writeRawInput(mi, inp) {
  try {
    mi.add(0x00).writeU32(state.rawInput.flags >>> 0);
    mi.add(0x04).writeFloat(inp.x);
    mi.add(0x08).writeFloat(inp.y);
  } catch (e) {
    state.lastError = String(e);
  }
}

// 实验用状态位覆盖：把 mask OR 进 ActorDataFlag 的第 word 个 u64，两份都写。
// 注意：ADF bit1/bit3（按 Java ActorFlags 的 sneaking/sprinting）实测对速度**无影响**，
// 真正改变速度的位见 docs/offsets §4.1（语义未知的经验表）。
function applyStateFlags() {
  const extra = state.adfOverride;
  if (!extra) return;
  let wrote = false;
  try {
    const client = freshComponent(CID.actorDataFlag, STRIDE.actorDataFlag);
    if (client) {
      state.playerAdf = client;
      wrote = orAdfMask(client, extra.word, extra.mask) || wrote;
    }
    if (state.serverAdf) {
      wrote = orAdfMask(state.serverAdf, extra.word, extra.mask) || wrote;
    }
  } catch (e) {
    state.lastError = String(e);
  }
  if (wrote) state.stateFlagsApplied++;
}

// 移动状态的**语义**编码，由真实按键标定得到（见 driver/probe_sprint.py 与 docs §4.1）：
//   潜行（真实按住 shift+w）——**已验证**：把输入向量缩到 0.3 即可复现原版 0.3 倍速；
//       同时真实潜行还会置 ADF word0 bit1、ADF word1 bit9、MoveInput +0x60 bit0/bit3，
//       这些是状态/姿态位（速度倍率来自输入缩放，不是它们）。
//   疾跑（真实按住 ctrl+w）——**尚未复现**：真实疾跑会把下面这些都置上，但注入后速度仍是
//       0.2159（= 走路），说明 1.3 倍不是由这些位驱动的，多半来自服务端 sprint 状态
//       （PlayerInputRequest / PlayerActionPacket 那条路径，我们还没接）：
//         MoveInput +0x00 raw flags bit8、+0x54 bit1、+0x60 bit1、ADF word0 bit3。
//       在找到真正的驱动位之前，move_flags("sprint") 会明确报错，避免产出"看起来在跑、
//       其实还是走"的假数据。
const MOVE_FLAGS = {
  sneak: { bits16: 0x09, adf: [[0, 0x02], [1, 0x200]] },
};
const MOVE_FLAGS_KNOWN_UNSUPPORTED = {
  sprint: "真实疾跑置 ADF bit3 / +0x00 bit8 / +0x54 bit1 / +0x60 bit1，但注入后仍为走路速度；1.3 倍来源未定位（见 docs §4.1）",
};

function applyMoveFlagsInputFlags() {
  // 必须在**输入解析**的时机写 +0x60：这个标志字游戏每 tick 都会重算，
  // 在 post-move 阶段写会被下一 tick 的解析覆盖掉，看起来就像"完全没生效"。
  const spec = MOVE_FLAGS[state.moveMode];
  if (!spec) return;
  try {
    if (state.playerMoveInput) {
      if (spec.rawFlags) {
        const raw = state.playerMoveInput.add(0x00);
        raw.writeU32(raw.readU32() | spec.rawFlags);
      }
      const at = state.playerMoveInput.add(MI.flags16);
      at.writeU16(at.readU16() | spec.bits16);
      if (spec.status54) {
        const st = state.playerMoveInput.add(0x54);
        st.writeU32(st.readU32() | spec.status54);
      }
    }
    if (state.serverMoveInput) {
      if (spec.rawFlags) {
        const raw = state.serverMoveInput.add(0x00);
        raw.writeU32(raw.readU32() | spec.rawFlags);
      }
      const at = state.serverMoveInput.add(MI.flags16);
      at.writeU16(at.readU16() | spec.bits16);
      if (spec.status54) {
        const st = state.serverMoveInput.add(0x54);
        st.writeU32(st.readU32() | spec.status54);
      }
    }
  } catch (e) {
    state.lastError = String(e);
  }
}

function applyMoveFlagsAdf() {
  // ADF 不会被每 tick 重算，放在 post-move 写就够了。
  const spec = MOVE_FLAGS[state.moveMode];
  if (!spec) return;
  try {
    for (const [word, mask] of spec.adf) {
      const client = freshComponent(CID.actorDataFlag, STRIDE.actorDataFlag);
      if (client) {
        state.playerAdf = client;
        orAdfMask(client, word, mask);
      }
      if (state.serverAdf) orAdfMask(state.serverAdf, word, mask);
    }
    state.moveFlagsApplied++;
  } catch (e) {
    state.lastError = String(e);
  }
}

// 实验用：把 mask OR 进 ActorDataFlag 的第 word 个 u64（两份都写）。
function orAdfMask(adf, word, mask) {
  if (!adf || word < 0 || word > 2) return false;
  const at = adf.add(word * 8);
  const value = at.readU64().or(uint64(String(mask >>> 0)));
  at.writeU64(value);
  return true;
}

// ---------------------------------------------------------------------------
// hooks
// ---------------------------------------------------------------------------

function installHook(name, handlers) {
  if (!state.signature[name] || !state.signature[name].ok) {
    log('warn', `skip hook ${name}: signature mismatch`);
    return null;
  }
  try {
    const listener = Interceptor.attach(addr(VA[name]), handlers);
    state.hooks[name] = listener;
    log('info', `hook installed: ${name} @ ${hex(VA[name])}`);
    return listener;
  } catch (e) {
    log('error', `hook failed: ${name}`, String(e));
    return null;
  }
}

function installHooks() {
  // registry 捕获：每次取 storage 时记录第一个参数。
  // 只有玩家未解析时才做 Map 查询；解析后 onEnter 立即返回。
  installHook('enttStorageGet', {
    onEnter(args) {
      state.getterCalls++;
      try {
        const reg = args[0];
        if (
          !reg.isNull() &&
          state.registryCandidates.size < 8 &&
          !state.registryCandidates.has(reg.toString())
        ) {
          state.registryCandidates.set(reg.toString(), reg);
          state.registryFrom = args[1].toUInt32();
        }
      } catch (e) {
        // 忽略
      }
    },
  });

  // 每 tick 一次：tick 计数、采样、延迟写操作、玩家刷新。
  installHook('localPlayerPostMoveTick', {
    onEnter() {
      state.tickCount++;
      try {
        if (state.registry === null || state.player === null || state.tickCount % 100 === 0) {
          refreshPlayer(true);
        }
        if (state.player !== null) {
          refreshServerPointers();
          if (state.pendingServerTp) {
            const pending = state.pendingServerTp;
            const attempt = serverTeleport(pending.pos[0], pending.pos[1], pending.pos[2]);
            if (attempt.ok) {
              state.pendingServerTp = null;
              state.lastTp = attempt;
            } else if (state.tickCount - pending.queuedTick > 60) {
              state.pendingServerTp = null;
              state.lastTp = attempt;
            }
          }
          applyPendingTp();
          if (state.jumpOnce) {
            state.jumpOnce = false;
            doJumpRequest();
          }
          if (state.input.jump) doJumpRequest();
          applyLookOverride();
          applyInputOverride();
          applyStateFlags();
          applyMoveFlagsAdf();
          if (state.recording.active) {
            if (state.recording.samples.length < state.recording.max) {
              state.recording.samples.push(snapshotSample());
            } else {
              state.recording.dropped++;
            }
          }
        }
      } catch (e) {
        state.lastError = String(e);
      }
    },
  });

  // 输入注入点：三个 MoveInput_ResolveOneEntity 变体都会调用它，
  // 返回值 buffer = {resolvedX, resolvedY}。onLeave 覆盖结果即可。
  installHook('moveInputResolve', {
    onEnter(args) {
      state.resolverCalls++;
      this.out = args[0];
      const client = state.playerMoveInput;
      const server = state.serverMoveInput;
      this.isServer =
        server !== null && !server.isNull() && args[1].equals(server);
      this.isPlayer =
        (client !== null && !client.isNull() && args[1].equals(client)) ||
        this.isServer;
      if (this.isServer) state.serverResolverCalls++;
    },
    onLeave() {
      if (!this.isPlayer) return;
      applyLookOverride();
      // 透传模式下**不要**覆盖解析结果：这个 hook 每 tick 都会把 state.input
      // （默认 0）写进 MoveInput，是一次性覆盖游戏刚解析出的真实按键的地方。
      // 标定/手动操作时要开 input_passthrough，否则按 WASD 看起来"没反应"。
      if (state.inputPassthrough) return;
      try {
        this.out.writeFloat(state.input.x);
        this.out.add(4).writeFloat(state.input.y);
        // 疾跑/潜行的输入侧标志必须在这一刻（解析结果刚出来）补上。
        applyMoveFlagsInputFlags();
        applyMoveFlagsAdf();
      } catch (e) {
        state.lastError = String(e);
      }
    },
  });

  if (state.signature.enttEmplace && state.signature.enttEmplace.ok) {
    state.emplaceFn = new NativeFunction(addr(VA.enttEmplace), 'pointer', [
      'pointer',
      'pointer',
      'pointer',
      'uint8',
    ]);
  }

  installHook('moveInputPipeline', {
    onEnter() {
      state.pipelineCalls++;
    },
  });

  // 方块查询包装：参数里通常带 BlockSource/Dimension 指针。
  // 只记录候选，不改变任何行为。
  installHook('blockQuery', {
    onEnter(args) {
      state.blockQueryCalls++;
      const pairs = [
        ['a0', args[0]],
        ['a1', args[1]],
        ['a2', args[2]],
        ['a3', args[3]],
      ];
      for (const [name, p] of pairs) {
        if (p.isNull()) continue;
        const key = p.toString();
        if (state.blockQueryCandidates.has(key)) continue;
        if (state.blockQueryCandidates.size >= 12) continue;
        let vtable = null;
        let inModule = false;
        try {
          vtable = p.readPointer();
          inModule = vtable.compare(base) >= 0 && vtable.compare(moduleEnd) < 0;
        } catch (e) {
          vtable = null;
        }
        state.blockQueryCandidates.set(key, {
          arg: name,
          ptr: key,
          vtable: vtable ? vtable.toString() : null,
          vtable_in_module: inModule,
        });
      }
      state.lastBlockQuery = {
        a0: args[0].toString(),
        a1: args[1].toString(),
        a2: args[2].toString(),
        a3: args[3].toString(),
        tick: state.tickCount,
      };

      // 从查询上下文推导并缓存方块访问器：
      // [ctx+456] -> 虚表+96() = accessor， accessor 虚表+16(BlockPos) = Block*。
      if (!state.blockAccessor || state.blockQueryCalls % 200 === 0) {
        try {
          const holder = args[3].add(456).readPointer();
          if (!holder.isNull()) {
            const holderVtable = holder.readPointer();
            const getAccessorFn = holderVtable.add(96).readPointer();
            const getAccessor = new NativeFunction(getAccessorFn, 'pointer', ['pointer']);
            const accessor = getAccessor(holder);
            if (!accessor.isNull()) {
              state.blockHolder = holder;
              state.blockAccessor = accessor;
              state.blockAccessorVtable = accessor.readPointer();
            }
          }
        } catch (e) {
          // 保持上一次缓存
        }
      }
    },
    onLeave(retval) {
      state.lastBlockQueryRet = retval.toString();
    },
  });
}

// ---------------------------------------------------------------------------
// RPC
// ---------------------------------------------------------------------------

function buildStatus() {
  return {
    ok: true,
    module: moduleName,
    base: base ? base.toString() : null,
    signatures: state.signature,
    registry: state.registry ? state.registry.toString() : null,
    registry_candidates: Array.from(state.registryCandidates.keys()),
    player: state.player === null ? null : hex(state.player),
    in_world: state.inWorld,
    tick: state.tickCount,
    getter_calls: state.getterCalls,
    pipeline_calls: state.pipelineCalls,
    resolver_calls: state.resolverCalls,
    server_resolver_calls: state.serverResolverCalls,
    block_query_calls: state.blockQueryCalls,
    block_query_candidates: Array.from(state.blockQueryCandidates.values()),
    block_accessor: state.blockAccessor ? state.blockAccessor.toString() : null,
    last_block: state.lastBlock,
    last_block_query: state.lastBlockQuery,
    last_block_query_ret: state.lastBlockQueryRet,
    input: state.input,
    look: state.look,
    move_input: state.playerMoveInput ? state.playerMoveInput.toString() : null,
    server_move_input: state.serverMoveInput ? state.serverMoveInput.toString() : null,
    server_rot: state.serverRot ? state.serverRot.toString() : null,
    input_applied_ticks: state.inputAppliedTicks,
    input_passthrough: state.inputPassthrough,
    move_mode: state.moveMode,
    move_flags_applied: state.moveFlagsApplied,
    state_flags_applied: state.stateFlagsApplied,
    adf_override: state.adfOverride,
    raw_input: state.rawInput,
    jump_once: state.jumpOnce,
    recording: {
      active: state.recording.active,
      count: state.recording.samples.length,
      max: state.recording.max,
      dropped: state.recording.dropped,
    },
    last_tp: state.lastTp,
    pending_server_tp: state.pendingServerTp ? state.pendingServerTp.pos : null,
    last_jump: state.lastJump,
    last_error: state.lastError,
  };
}

rpc.exports = {
  ping() {
    return 'pong';
  },

  init() {
    try {
      resolveBase();
      const ok = verifySignatures();
      installHooks();
      log('info', 'agent initialized', {
        base: base.toString(),
        module: moduleName,
        signatures_ok: ok,
      });
      return { ok: true, base: base.toString(), module: moduleName, signatures_ok: ok };
    } catch (e) {
      state.lastError = String(e);
      return { ok: false, error: String(e) };
    }
  },

  status() {
    return buildStatus();
  },

  diagnose() {
    try {
      resolveBase();
      refreshPlayer(true);
      const storages = {};
      const components = {};
      for (const [name, id] of Object.entries(CID)) {
        const st = storageFor(id);
        storages[name] = st
          ? { id: hex(id), ptr: st.toString(), count: packedEntities(st).length }
          : null;
      }
      const strides = {
        stateVector: STRIDE.stateVector,
        actorRotation: STRIDE.actorRotation,
        actorDataFlag: STRIDE.actorDataFlag,
        moveInput: STRIDE.moveInput,
      };
      for (const [name, stride] of Object.entries(strides)) {
        const ptr = component(CID[name], stride);
        components[name] = ptr ? ptr.toString() : null;
      }
      const og = hasComponent(CID.onGroundFlag);
      components.onGroundFlag = og ? 'present' : null;
      return {
        ok: true,
        base: base.toString(),
        module: moduleName,
        signatures: state.signature,
        registry_candidates: Array.from(state.registryCandidates.keys()),
        registry: state.registry ? state.registry.toString() : null,
        player: state.player === null ? null : hex(state.player),
        tick: state.tickCount,
        storages,
        components,
        state: readState(),
        status: buildStatus(),
      };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  getTickCount() {
    return state.tickCount;
  },

  serverState() {
    try {
      const ctx = findServerContext();
      if (!ctx) return { ok: false, error: 'server registry not found' };
      const sv = componentAddr(
        findStorage(ctx.registry, CID.stateVector),
        ctx.entity,
        STRIDE.stateVector,
      );
      if (!sv) return { ok: false, error: 'server StateVectorComponent missing' };
      const eye = readVec3(sv.add(SV.pos));
      const rot = componentAddr(
        findStorage(ctx.registry, CID.actorRotation),
        ctx.entity,
        STRIDE.actorRotation,
      );
      return {
        ok: true,
        registry: ctx.registry.toString(),
        entity: hex(ctx.entity),
        pos: [eye[0], eye[1] - EYE_HEIGHT, eye[2]],
        eye_pos: eye,
        vel: readVec3(sv.add(SV.vel)),
        yaw: rot ? rot.add(ROT.yaw).readFloat() : null,
        pitch: rot ? rot.add(ROT.pitch).readFloat() : null,
      };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  // 按方块坐标读取方块。返回 name_hash（FNV-1-64("minecraft:xxx")）。
  // 依赖至少一次已捕获的方块查询（移动/碰撞时会产生），accessor 会被缓存。
  blockAt(x, y, z) {
    try {
      if (!state.blockAccessor) {
        return { ok: false, error: 'no block query captured yet; move or collide first' };
      }
      const vtable = state.blockAccessorVtable || state.blockAccessor.readPointer();
      const getBlockFn = vtable.add(16).readPointer();
      const getBlock = new NativeFunction(getBlockFn, 'pointer', ['pointer', 'pointer']);
      const blockPos = Memory.alloc(12);
      blockPos.writeS32(Number(x) | 0);
      blockPos.add(4).writeS32(Number(y) | 0);
      blockPos.add(8).writeS32(Number(z) | 0);
      const block = getBlock(state.blockAccessor, blockPos);
      const pos = [Number(x) | 0, Number(y) | 0, Number(z) | 0];
      if (block.isNull()) {
        state.lastBlock = { ok: true, pos, block: null, name_hash: null, air: false };
        return state.lastBlock;
      }
      const legacy = block.add(0x68).readPointer();
      let nameHash = null;
      let name = null;
      if (!legacy.isNull()) {
        nameHash = legacy.add(0xe0).readU64().toString();
        name = readStdString(legacy, 0xe8);
      }
      state.lastBlock = {
        ok: true,
        pos,
        block: block.toString(),
        legacy: legacy.isNull() ? null : legacy.toString(),
        name_hash: nameHash,
        name,
        air: nameHash === '13643738286110291016',
      };
      return state.lastBlock;
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  // 诊断：用最近一次方块查询的上下文推导 BlockSource/BlockAccessor，
  // 再按 BlockPos 调虚表+16 取方块。成功后可以扩展成 blockAt/scan。
  probeBlockAt(x, y, z) {
    try {
      const last = state.lastBlockQuery;
      if (!last) return { ok: false, error: 'no block query captured; move/collide first' };
      const ctx = ptr(last.a3);
      const holder = ctx.add(456).readPointer();
      if (holder.isNull()) return { ok: false, error: 'query ctx +456 is null' };
      const holderVtable = holder.readPointer();
      const getAccessorFn = holderVtable.add(96).readPointer();
      const getAccessor = new NativeFunction(getAccessorFn, 'pointer', ['pointer']);
      const accessor = getAccessor(holder);
      if (accessor.isNull()) return { ok: false, error: 'accessor is null' };
      const accessorVtable = accessor.readPointer();
      const getBlockFn = accessorVtable.add(16).readPointer();
      const getBlock = new NativeFunction(getBlockFn, 'pointer', ['pointer', 'pointer']);
      const blockPos = Memory.alloc(12);
      blockPos.writeS32(Number(x) | 0);
      blockPos.add(4).writeS32(Number(y) | 0);
      blockPos.add(8).writeS32(Number(z) | 0);
      const block = getBlock(accessor, blockPos);
      let head = null;
      if (!block.isNull()) {
        const bytes = new Uint8Array(block.readByteArray(64));
        head = Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join(' ');
      }
      return {
        ok: true,
        ctx: ctx.toString(),
        holder: holder.toString(),
        holder_vtable: holderVtable.toString(),
        get_accessor: getAccessorFn.toString(),
        accessor: accessor.toString(),
        accessor_vtable: accessorVtable.toString(),
        get_block: getBlockFn.toString(),
        block_pos: [Number(x) | 0, Number(y) | 0, Number(z) | 0],
        block: block.isNull() ? null : block.toString(),
        block_head: head,
      };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },
  // 这样不必（也很难）改服务端的相机朝向。
  // 用世界坐标方向设置移动输入：按服务端当前 yaw 反算 forward/strafe。
  moveWorld(dx, dz, yawOverride) {
    try {
      let [wx, wz] = [Number(dx), Number(dz)];
      if (!Number.isFinite(wx) || !Number.isFinite(wz)) {
        return { ok: false, error: 'world direction must be finite' };
      }
      const len = Math.hypot(wx, wz);
      if (len > 1) {
        wx /= len;
        wz /= len;
      }

      let yaw = Number(yawOverride);
      if (!Number.isFinite(yaw)) {
        const ctx = findServerContext();
        let rot = null;
        if (ctx) {
          rot = componentAddr(
            findStorage(ctx.registry, CID.actorRotation),
            ctx.entity,
            STRIDE.actorRotation,
          );
        }
        if (!rot) rot = freshComponent(CID.actorRotation, STRIDE.actorRotation);
        yaw = rot ? rot.add(ROT.yaw).readFloat() : 0;
      }

      const rad = (yaw * Math.PI) / 180;
      const cos = Math.cos(rad);
      const sin = Math.sin(rad);
      // 游戏内：worldX = strafe*cos - forward*sin, worldZ = strafe*sin + forward*cos
      const strafe = wx * cos + wz * sin;
      const forward = -wx * sin + wz * cos;
      state.input = {
        x: Math.max(-1, Math.min(1, strafe)),
        y: Math.max(-1, Math.min(1, forward)),
        jump: false,
        sneak: false,
        sprint: false,
      };
      return { ok: true, yaw, strafe: state.input.x, forward: state.input.y, world: [wx, wz] };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  // 在服务端 registry 上排队一个位置请求。服务端权威，成功后客户端会被同步。
  serverTp(x, y, z) {
    try {
      return serverTeleport(x, y, z);
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  // 直接写服务端 StateVector（诊断用，可能被服务端移动系统覆盖）。
  serverTpDirect(x, y, z) {
    try {
      const pos = [Number(x), Number(y), Number(z)];
      if (!pos.every(Number.isFinite)) return { ok: false, error: 'position must be finite' };
      const ctx = findServerContext();
      if (!ctx) return { ok: false, error: 'server registry not found' };
      const sv = componentAddr(
        findStorage(ctx.registry, CID.stateVector),
        ctx.entity,
        STRIDE.stateVector,
      );
      if (!sv) return { ok: false, error: 'server StateVectorComponent missing' };
      writeVec3(sv.add(SV.pos), [pos[0], pos[1] + EYE_HEIGHT, pos[2]]);
      sv.add(SV.vel).writeFloat(0);
      sv.add(SV.vel + 4).writeFloat(0);
      sv.add(SV.vel + 8).writeFloat(0);
      return {
        ok: true,
        registry: ctx.registry.toString(),
        entity: hex(ctx.entity),
        pos,
      };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  getState() {
    return readState();
  },

  // 诊断：按绝对地址/模块 VA 读内存。
  dumpMemory(address, size) {
    try {
      const p = typeof address === 'string' ? ptr(address) : ptr(Number(address));
      const n = Math.max(1, Math.min(4096, Number(size) || 64));
      const bytes = new Uint8Array(p.readByteArray(n));
      let out = '';
      for (let i = 0; i < bytes.length; i++) {
        out += bytes[i].toString(16).padStart(2, '0');
        if (i % 16 === 15) out += '\n';
        else if (i % 4 === 3) out += ' ';
      }
      return { ok: true, address: p.toString(), size: n, hex: out };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  dumpGlobal(va, size) {
    try {
      const p = addr(Number(va));
      const n = Math.max(1, Math.min(4096, Number(size) || 64));
      const bytes = new Uint8Array(p.readByteArray(n));
      let out = '';
      for (let i = 0; i < bytes.length; i++) {
        out += bytes[i].toString(16).padStart(2, '0');
        if (i % 16 === 15) out += '\n';
        else if (i % 4 === 3) out += ' ';
      }
      return { ok: true, address: p.toString(), size: n, hex: out };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  // 诊断用：列出每个候选 registry 里各组件的存储和实体，寻找服务端 registry。
  inspectRegistries() {
    try {
      const ids = {
        ...CID,
        teleportToRequest: 0xf8bdf31c,
        localConstBlockSourceFactory: 0xc695b914,
        localSpatialEntityFetcherFactory: 0x5c4c91cf,
        blockCollisionEvaluationQueue: 0xef62451a,
      };
      const out = [];
      for (const [key, reg] of state.registryCandidates) {
        const components = {};
        for (const [name, id] of Object.entries(ids)) {
          const st = findStorage(reg, id);
          if (!st) {
            components[name] = null;
            continue;
          }
          const entities = packedEntities(st);
          components[name] = {
            id: hex(id),
            ptr: st.toString(),
            count: entities.length,
            entities: entities.slice(0, 8).map(hex),
          };
        }
        out.push({
          registry: key,
          localPlayers: components.localPlayer ? components.localPlayer.entities : [],
          players: components.player ? components.player.entities : [],
          components,
        });
      }
      return { ok: true, registries: out };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  // {forward, strafe, jump, sneak, sprint}
  // forward/strafe 在 [-1,1]；sneak/sprint 目前只记录，不伪造状态位。
  setInput(input) {
    try {
      const num = (v) => {
        const n = Number(v);
        return Number.isFinite(n) ? Math.max(-1, Math.min(1, n)) : 0;
      };
      state.input = {
        x: num(input && input.strafe),
        y: num(input && input.forward),
        jump: !!(input && input.jump),
        sneak: !!(input && input.sneak),
        sprint: !!(input && input.sprint),
      };
      const warnings = [];
      if (state.input.sneak) {
        warnings.push('sneak 未生效：写 ADF bit1 实测无效，用 adf_override/raw_input 做实验');
      }
      if (state.input.sprint) {
        warnings.push('sprint 未生效：写 ADF bit3 实测无效，用 adf_override/raw_input 做实验');
      }
      if (!state.player) warnings.push('local player not resolved yet');
      applyStateFlags();
      return { ok: true, applied: state.input, warnings };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  // 实验用：是否连同原始输入字段一起写；flags 是 +0x00 的 dword 值。
  rawInput(on, flags) {
    state.rawInput = on ? { flags: Number(flags) || 0 } : null;
    return { ok: true, raw_input: state.rawInput };
  },

  // 实验用：持续把 ADF 某一位 OR 进去（word 0..2，bit 0..63）。传 null 取消。
  adfOverride(word, bit) {
    try {
      if (word === null || word === undefined) {
        state.adfOverride = null;
        return { ok: true, override: null };
      }
      const w = Number(word);
      const b = Number(bit);
      if (!Number.isInteger(w) || w < 0 || w > 2) {
        return { ok: false, error: 'word must be 0..2' };
      }
      if (!Number.isInteger(b) || b < 0 || b > 63) {
        return { ok: false, error: 'bit must be 0..63' };
      }
      state.adfOverride = { word: w, bit: b, mask: Math.pow(2, b) };
      return { ok: true, override: state.adfOverride };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  setYawPitch(yaw, pitch) {
    try {
      if (refreshPlayer(false) === null) return { ok: false, error: 'local player not resolved' };
      const rot = freshComponent(CID.actorRotation, STRIDE.actorRotation);
      if (!rot) return { ok: false, error: 'ActorRotationComponent missing' };
      const y = Number(yaw);
      const p = Number(pitch);
      if (!Number.isFinite(y) || !Number.isFinite(p)) {
        return { ok: false, error: 'yaw/pitch must be finite numbers' };
      }
      rot.add(ROT.pitch).writeFloat(p);
      rot.add(ROT.yaw).writeFloat(y);
      rot.add(ROT.prevPitch).writeFloat(p);
      rot.add(ROT.prevYaw).writeFloat(y);
      state.look = { yaw: y, pitch: p };
      return { ok: true, yaw: y, pitch: p, pinned: true };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  setVel(vx, vy, vz) {
    try {
      if (refreshPlayer(false) === null) return { ok: false, error: 'local player not resolved' };
      const sv = freshComponent(CID.stateVector, STRIDE.stateVector);
      if (!sv) return { ok: false, error: 'StateVectorComponent missing' };
      state.playerSync = sv;
      const v = [Number(vx), Number(vy), Number(vz)];
      if (!v.every(Number.isFinite)) return { ok: false, error: 'velocity must be finite' };
      writeVec3(sv.add(SV.vel), v);
      return { ok: true, vel: v };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  tp(x, y, z, mode) {
    try {
      const pos = [Number(x), Number(y), Number(z)];
      if (!pos.every(Number.isFinite)) return { ok: false, error: 'position must be finite' };
      if (mode === 'server') {
        const result = serverTeleport(pos[0], pos[1], pos[2]);
        if (result.ok) return result;
        if (result.error === 'server registry not found') {
          state.pendingServerTp = { pos, queuedTick: state.tickCount };
          return {
            ok: true,
            queued: true,
            via: 'server',
            pos,
            note: '等待服务端 registry 后重试',
          };
        }
        return result;
      }
      state.pendingTp = { pos, mode: mode || 'request' };
      return {
        ok: true,
        queued: true,
        pos,
        mode: state.pendingTp.mode,
        tick: state.tickCount,
        warning:
          '客户端写位置会被服务端权威位置覆盖；可靠传送请用游戏内 /tp 命令（driver 的 setup.commands 路径）',
      };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },

  jump() {
    state.jumpOnce = true;
    return { ok: true, queued: true };
  },

  stopInput() {
    state.input = { x: 0, y: 0, jump: false, sneak: false, sprint: false };
    state.moveMode = null;
    applyInputOverride();
    applyStateFlags();
    return { ok: true };
  },

  // 移动状态注入（疾跑/潜行）：语义编码见 MOVE_FLAGS，标定见 driver/probe_sprint.py。
  moveFlags(mode) {
    const name = mode === null || mode === undefined ? 'none' : String(mode);
    if (name === 'none' || name === '') {
      state.moveMode = null;
      return { ok: true, mode: null };
    }
    if (!MOVE_FLAGS[name]) {
      const known = MOVE_FLAGS_KNOWN_UNSUPPORTED[name];
      return {
        ok: false,
        error: known
          ? `move mode ${name} 尚未复现：${known}`
          : `unknown move mode ${name}`,
        known: Object.keys(MOVE_FLAGS),
      };
    }
    state.moveMode = name;
    return { ok: true, mode: name };
  },

  clearLook() {
    state.look = null;
    return { ok: true };
  },

  startRecording(maxTicks, includeRaw) {
    const max = Math.max(1, Math.min(100000, Number(maxTicks) || 1));
    state.includeRaw = !!includeRaw;
    state.recording = {
      active: true,
      max,
      includeRaw: state.includeRaw,
      samples: [],
      dropped: 0,
      startedTick: state.tickCount,
    };
    return { ok: true, start_tick: state.tickCount, max, include_raw: state.includeRaw };
  },

  stopRecording() {
    const rec = state.recording;
    rec.active = false;
    state.includeRaw = false;
    return {
      ok: true,
      start_tick: rec.startedTick,
      end_tick: state.tickCount,
      include_raw: !!rec.includeRaw,
      samples: rec.samples,
      dropped: rec.dropped,
    };
  },

  // 标定用：一次性 dump 两套 registry 里玩家组件的原始字节。
  dumpComponents() {
    const raw = readRawComponents();
    if (!raw) return { ok: false, error: 'player not resolved' };
    return { ok: true, ...raw };
  },

  // 标定用：打开后 agent 不再写 MoveInput，真实按键可以透传到游戏。
  inputPassthrough(on) {
    state.inputPassthrough = !!on;
    return { ok: true, input_passthrough: state.inputPassthrough };
  },

  // 标定用：指定要整块 dump 的组件 id（0 或 null 取消）。逐 tick 采样会带上 raw.storage。
  setDumpCid(cid, length) {
    if (cid === null || cid === undefined || Number(cid) === 0) {
      state.dumpCid = null;
      return { ok: true, dump_cid: null };
    }
    state.dumpCid = Number(cid) >>> 0;
    state.dumpLen = Math.max(16, Math.min(8192, Number(length) || 2048));
    return { ok: true, dump_cid: `0x${state.dumpCid.toString(16)}`, dump_len: state.dumpLen };
  },
};
