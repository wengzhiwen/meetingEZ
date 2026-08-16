import Foundation

// ---- 控制协议 v1 合同（冻结） ----
//
// WebSocket 文本帧 = JSON（每条必带 "type"，可选 "ref" 由客户端透传、回复原样带回）：
//   C→S: ping / requestPermission / listApps / start{mode:"apps",bundleIds}|{mode:"system"} / stop
//   S→C: hello{version,permission,mock} / pong / permission{granted,effective} /
//        apps[{pid,bundleId,name}] / started{mode,sources,audioFormat{sampleRate,channels}} /
//        stopped{reason,message} / error{code,message} / stats{framesSent,framesDropped}
// 二进制帧 = Float32 LE、单声道、started.audioFormat.sampleRate（固定 16000），约 1600 样本/帧。
// 浏览器发来的二进制帧一律忽略。

let protocolVersion = 1
let defaultPort: UInt16 = 17642

struct AudioFormat: Equatable {
    var sampleRate: Int
    var channels: Int

    var json: [String: Any] {
        ["sampleRate": sampleRate, "channels": channels]
    }

    /// 线上合同固定 16k 单声道：即使 SCK 交付其他格式，也在采集器内转换后再出帧。
    static let wire = AudioFormat(sampleRate: 16000, channels: 1)
}

struct AppEntry {
    var pid: pid_t
    var bundleId: String
    var name: String

    var json: [String: Any] {
        ["pid": Int(pid), "bundleId": bundleId, "name": name]
    }
}

enum CaptureMode: Equatable {
    case system
    case apps(bundleIds: [String])

    var name: String {
        if case .system = self { return "system" }
        return "apps"
    }
}

struct CollectorError: Error, CustomStringConvertible {
    let code: String
    let message: String

    init(_ code: String, _ message: String) {
        self.code = code
        self.message = message
    }

    var description: String { "\(code): \(message)" }

    static func noPermission() -> CollectorError {
        CollectorError("no-permission", "需要「屏幕录制」权限（macOS 将本采集器视为屏幕录制）。")
    }
    static func busy() -> CollectorError {
        CollectorError("busy", "上一次启动/停止操作尚未完成。")
    }
    static func invalidParams(_ message: String) -> CollectorError {
        CollectorError("invalid-params", message)
    }
    static func startFailed(_ message: String) -> CollectorError {
        CollectorError("start-failed", message)
    }
}

/// 音频源抽象：真实 ScreenCaptureKit 与 --mock-audio 共用同一接口。
protocol CaptureSource: AnyObject {
    var isMock: Bool { get }
    var isCapturing: Bool { get }

    /// 已聚合好的 Float32 帧回调（wire 格式，16k mono，~100ms）。
    var onFrame: ((Data) -> Void)? { get set }
    /// 采集中意外停止（reason != "request"）。
    var onStopped: ((_ reason: String, _ message: String) -> Void)? { get set }

    func preflightPermission() -> Bool
    func requestPermission()
    /// TCC 显示已授权后，实际验证本进程是否已生效（部分 macOS 版本需重启进程）。
    func verifyEffectivePermission(_ completion: @escaping (Bool) -> Void)
    func listApplications(completion: @escaping ([AppEntry]) -> Void)
    func start(mode: CaptureMode, completion: @escaping (Result<AudioFormat, Error>) -> Void)
    func stop(completion: (() -> Void)?)
}

func jsonLine(_ object: [String: Any]) -> String {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
          let text = String(data: data, encoding: .utf8) else { return "{}" }
    return text
}
