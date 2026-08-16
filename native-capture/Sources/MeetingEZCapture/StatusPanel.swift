import SwiftUI

extension Notification.Name {
    /// 实时音频电平（约 250ms 一次，仅采集中），userInfo:
    /// ["rms": Double, "framesSent": Int, "framesDropped": Int]
    static let captureAudioLevel = Notification.Name("captureAudioLevel")
}

/// 状态面板数据：聚合服务端通知 + 2s 轮询 TCC 权限。只读展示，无配置。
final class CollectorStatusModel: ObservableObject {
    enum PermissionState {
        case unknown
        case granted
        case needsRestart
        case denied
    }

    @Published var serverAddress = ""
    @Published var serverRunning = false
    @Published var permissionState: PermissionState = .unknown
    @Published var clientConnected = false
    @Published var capturing = false
    @Published var captureDetail = ""
    @Published var level: Double = 0
    @Published var framesSent = 0
    @Published var framesDropped = 0

    private weak var source: CaptureSource?
    private var pollTimer: Timer?

    func start(port: UInt16, source: CaptureSource) {
        self.source = source
        serverAddress = "ws://127.0.0.1:\(port)"
        serverRunning = true
        refreshPermission()

        let center = NotificationCenter.default
        center.addObserver(forName: .captureStateDidChange, object: nil, queue: .main) { [weak self] note in
            guard let self else { return }
            self.capturing = note.userInfo?["capturing"] as? Bool ?? false
            self.captureDetail = note.userInfo?["detail"] as? String ?? ""
            if !self.capturing { self.level = 0 }
        }
        center.addObserver(forName: .captureClientStateDidChange, object: nil, queue: .main) { [weak self] note in
            self?.clientConnected = note.userInfo?["connected"] as? Bool ?? false
        }
        center.addObserver(forName: .captureAudioLevel, object: nil, queue: .main) { [weak self] note in
            guard let self else { return }
            if let rms = note.userInfo?["rms"] as? Double {
                // ×2.5 拉开语音区间的显示刻度，加平滑防止跳变。
                let target = min(1.0, rms * 2.5)
                self.level = self.level * 0.6 + target * 0.4
            }
            self.framesSent = note.userInfo?["framesSent"] as? Int ?? self.framesSent
            self.framesDropped = note.userInfo?["framesDropped"] as? Int ?? self.framesDropped
        }
        center.addObserver(forName: .capturePermissionNeedsRestart, object: nil, queue: .main) { [weak self] _ in
            // TCC 已授权但需重启进程才生效，本进程内保持该状态。
            self?.permissionState = .needsRestart
        }

        pollTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            self?.refreshPermission()
        }
    }

    private func refreshPermission() {
        guard let source else { return }
        if source.preflightPermission() {
            if permissionState != .needsRestart { permissionState = .granted }
        } else {
            permissionState = .denied
        }
    }
}

/// 最小状态面板：服务 / 权限 / Web 客户端 / 采集 / 电平与帧统计。
struct StatusPanelView: View {
    @ObservedObject var model: CollectorStatusModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 6) {
                Image(systemName: "waveform")
                    .foregroundStyle(.tint)
                Text("MeetingEZ Capture").font(.headline)
                Spacer()
                Text(model.serverRunning ? "运行中" : "未运行")
                    .font(.caption)
                    .foregroundStyle(model.serverRunning ? .green : .red)
            }

            Divider()

            StatusRow(label: "服务") {
                Text(model.serverAddress)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }

            StatusRow(label: "屏幕录制权限") {
                switch model.permissionState {
                case .granted:
                    Label("已授权", systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.caption)
                case .needsRestart:
                    Label("已授权，重启采集器后生效", systemImage: "arrow.clockwise.circle.fill")
                        .foregroundStyle(.orange).font(.caption)
                case .denied:
                    Label("未授权（系统设置 → 隐私与安全性）", systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red).font(.caption)
                case .unknown:
                    Text("…").font(.caption).foregroundStyle(.secondary)
                }
            }

            StatusRow(label: "Web 客户端") {
                HStack(spacing: 4) {
                    Circle().fill(model.clientConnected ? Color.green : Color.secondary)
                        .frame(width: 8, height: 8)
                    Text(model.clientConnected ? "已连接" : "未连接")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            StatusRow(label: "采集") {
                Text(model.capturing ? "采集中 · \(model.captureDetail)" : "空闲")
                    .font(.caption)
                    .foregroundStyle(model.capturing ? .primary : .secondary)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("音频电平").font(.caption).foregroundStyle(.secondary)
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(.quaternary)
                        Capsule().fill(model.capturing ? Color.accentColor : Color.secondary)
                            .frame(width: max(3, geo.size.width * model.level))
                    }
                }
                .frame(height: 8)
            }

            StatusRow(label: "帧统计") {
                Text("已发 \(model.framesSent) · 丢弃 \(model.framesDropped)")
                    .font(.caption)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }

            Divider()

            Text("Web 客户端在 meetingEZ 实时页选择「应用 / 系统音频」后自动连接。"
                 + "此面板仅展示状态，采集配置均在 web 端进行。")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(width: 320)
    }
}

private struct StatusRow<Content: View>: View {
    let label: String
    @ViewBuilder var content: Content

    var body: some View {
        HStack(alignment: .top) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 92, alignment: .leading)
            content
            Spacer(minLength: 0)
        }
    }
}
