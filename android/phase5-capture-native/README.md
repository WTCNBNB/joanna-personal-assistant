# Joanna Phase5 Native Capture

原生 Android 版五期采集器。它是当前主采集链路，旧 `android/phase5-capture/` 仅保留为 HBuilderX/uni-app legacy 对照。

## 职责

- 手机只负责采集、切片、临时缓存和 WLAN 上传。
- 本机仍负责长期记忆、推理、画像、反馈和反思。
- 每 60 秒录制一个 WAV/PCM16 mono 片段，并上传同一时间窗 GPS 轨迹。
- 默认上传到本机局域网接收端：`http://YOUR_MAC_LAN_IP:18787/api/phase5/segments`。
- 不在同一局域网时，可改用自有 HTTPS 反向代理入口，例如 `https://YOUR_PUBLIC_DOMAIN/api/phase5/segments`；公网代理只负责转发，业务接收端和 SQLite 仍建议留在受控本机或私有服务器。
- 采集期间持有 `PARTIAL_WAKE_LOCK`，降低锁屏后分段、GPS 和上传被挂起的概率。
- 上传失败会缓存在 App 私有目录，下一次开始采集会自动重传，也可以在 App 中手动点“重传缓存”。

## 本机接收端

WLAN 模式下不要使用 `127.0.0.1` 或 `adb reverse`。本机启动：

```bash
python3 -m joanna.app.cli phase5 receive --host 0.0.0.0 --port 18787
```

手机端 URL 填：

```text
http://YOUR_MAC_LAN_IP:18787/api/phase5/segments
```

如果 Mac 局域网 IP 变化，用 `ipconfig getifaddr en0` 重新确认。

## 公网转发接收端

如果手机和 Mac 不在同一局域网，上传仍是普通 HTTP/HTTPS，不走 adb 无线调试。链路改为：

```text
Android App -> HTTPS reverse proxy -> private tunnel or VPN -> phase5 receive
```

公网暴露时必须启用上传 token，并优先使用 HTTPS：

```bash
PHASE5_UPLOAD_TOKEN='换成临时长随机值' \
python3 -m joanna.app.cli phase5 receive --host 0.0.0.0 --port 18787
```

当前也可以安装为 Mac 常驻 receiver：

```bash
scripts/phase5_install_receiver_daemon.sh
```

该脚本会把 receiver 运行时代码和五期数据根放到：

```text
~/.local/share/joanna-phase5/
```

并把项目内 `.joanna/phase5-weektest` 指向同一数据根。这样 launchd 不需要访问 `Documents/乔纳个人助手`，但项目内 CLI 仍可继续使用默认 `.joanna/phase5-weektest/phase5-weektest.db`。脚本会生成或复用：

```text
~/.local/share/joanna-phase5/phase5-weektest/upload-token.txt
```

查看常驻状态：

```bash
launchctl print gui/$(id -u)/io.joanna.phase5.receiver
lsof -nP -iTCP:18787 -sTCP:LISTEN
```

手机端 URL 填：

```text
https://YOUR_PUBLIC_DOMAIN/api/phase5/segments
```

App 首屏有“局域网”和“公网转发”两个 URL 快捷按钮。首次使用前请把示例 URL 改成自己的接收端地址；如果 receiver 启用了 token，把同一个 token 填到“上传 token”输入框。App 会通过 `X-Joanna-Phase5-Token` 请求头发送 token，避免 token 出现在反向代理 access log 的 URL 中。常驻模式下 token 可这样查看：

```bash
cat .joanna/phase5-weektest/upload-token.txt
```

receiver 仍兼容 `?token=...`，仅建议临时排查使用。如果 DNS 尚未生效，可先用服务器 IP 和隧道端口做排查：

```text
http://YOUR_SERVER_IP:18787/api/phase5/segments?token=换成同一个临时长随机值
```

公网验收顺序：

```bash
curl -H 'X-Joanna-Phase5-Token: 换成同一个临时长随机值' \
  https://YOUR_PUBLIC_DOMAIN/health
python3 -m joanna.app.cli phase5 segments list --limit 5
```

注意：接收端必须在线，隧道或 VPN 必须运行，`phase5 receive` 必须监听可被代理访问的地址。代理层不应保存音频、GPS 或 SQLite，只负责转发。

## 无线调试安装

DJI Mic 2 发射器占用手机 USB-C 时，开发安装和日志查看走 Android 开发者选项里的“无线调试”。这只是 adb 开发通道；App 上传音频和 GPS 仍走上面的普通 WLAN HTTP URL，不依赖无线调试。

手机端：

1. 打开“开发者选项 -> 无线调试”。
2. 选择“使用配对码配对设备”，记录手机显示的 IP、配对端口和配对码。
3. 配对后回到无线调试页，记录当前“IP 地址和端口”中的调试端口。

Mac 端：

```bash
cd android/phase5-capture-native
. ./dev-env.sh
adb pair 手机IP:配对端口
adb connect 手机IP:调试端口
adb devices -l
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

`adb devices -l` 看到 `device` 状态后才算开发通道可用。无线调试端口可能会变化，连接失败时以手机无线调试页面当前显示为准重新 `adb connect`。

## 开发环境

当前已配置：

- JDK 17。
- Android SDK，至少包含 `platform-tools`、`platforms;android-35` 和 `build-tools;35.0.0`。
- Gradle 可使用项目自带 wrapper：`./gradlew`。

```bash
. android/phase5-capture-native/dev-env.sh
java -version
adb version
gradle -version
```

常用构建：

```bash
cd android/phase5-capture-native
./gradlew :app:assembleDebug
```

构建产物：

```text
android/phase5-capture-native/app/build/outputs/apk/debug/app-debug.apk
```

如果安装了 Android Studio，也可以直接打开本目录，由 Android Studio 同步 Gradle 后运行 `app`。

## 运行与验收

1. 手机和 Mac 连接同一 WLAN。
2. 本机启动 `phase5 receive --host 0.0.0.0 --port 18787`。
3. 通过无线调试安装 debug APK 到小米 11 Ultra。
4. 在 App 中授权录音、定位、蓝牙和通知权限。
5. 刷新输入设备，选择 DJI Mic 2 / Bluetooth SCO 或 BLE 输入。
6. 点击开始采集，观察首屏实时电平图、`bytes/ok/zero/err`、`amp/max`、`bytePeak/nonZero`、实际 route、warning、最近上传状态和缓存待重传数量。`amp` 是当前音频块实时振幅，会升也会降；`max` 是当前 60 秒片段峰值，只升不降。
7. 至少连续上传 5 个 60 秒片段后，在本机运行：

```bash
python3 -m joanna.app.cli phase5 segments list --limit 5
```

最终成功标准不是设备枚举或 route metadata，而是 DJI Mic 2 发射器外壳敲击声明显高于手机外壳敲击声，并且本机 manifest 有 sha256、duration、GPS 点数和 route 信息。GPS 室内可能无卫星 fix，原生端会同时请求 `gps/network/fused/passive`，并在每个 60 秒片段开头补一次 15 分钟内的 last-known 位置。

## 上传契约

接口保持兼容：

- `POST /api/phase5/segments`
- multipart 字段：`metadata`、`gps`、`audio`
- 音频：WAV / PCM16LE / mono
- metadata 必含：`device_id`、`mic_label`、`segment_index`、`started_at`、`ended_at`、`selected_audio_device_id`、`selected_audio_device_name`、`route_type`、`actual_route_type`、`sample_rate`、`channels`、`codec`
- 原生端新增诊断字段：`capture_app=native_android`、`capture_client_version`、`network_mode=wlan`、`audio_bytes_written`、`read_success_count`、`read_error_count`、`byte_peak`、`non_zero_samples`、`gps_point_count`、`gps_active_providers`、`gps_provider_status`、`gps_live_point_count`、`gps_last_known_point_count`
- GPS JSON 的每个点包含 `provider`、`source=live|last_known` 和 `location_time_ms`，用于区分实时定位点与片段开头的 last-known 兜底点。
- 重传字段：`upload_attempt`、`cached_upload`、`client_cached_at`
