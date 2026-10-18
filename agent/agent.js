// frida agent for Minecraft Bedrock 1.26.45.01.
//
// Load: frida -p <pid> -l agent/agent.js
// 或由 driver/run.py 注入。
//
// 只做两件事：读状态、写状态。不合成键鼠输入。
// 每个 export 在对应钩子经过运行期验证后再加，未验证的先留 TODO。

rpc.exports = {
  ping() {
    return "pong";
  },

  // TODO: getState() -> { pos:[x,y,z], vel:[x,y,z], onGround, flags, ... }
  //   数据源：StateVectorComponent / ActorDataFlagComponent / 各组件存储。
  // TODO: tp(x, y, z) -> 走引擎提交路径，不直接改渲染位置。
  // TODO: setVel(vx, vy, vz)
  // TODO: setInput({ forward, strafe, jump, sneak, sprint })
  //   写输入组件，不模拟按键。
  // TODO: setYawPitch(yaw, pitch)
  //   直接写旋转，绕开 raw input 不生效的问题。
  // TODO: onTick(cb)
  //   逐 tick 回调，用于采样与到达序记录。
};
