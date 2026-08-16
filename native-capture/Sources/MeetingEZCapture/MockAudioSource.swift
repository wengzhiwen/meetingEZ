import Foundation

/// `--mock-audio` 假源：440Hz 正弦波 + 固定应用列表，权限恒为已授权。
/// 用于 Web 端在未授予屏幕录制权限时做端到端联调。
final class MockAudioSource: CaptureSource {
    let isMock = true
    private(set) var isCapturing = false

    var onFrame: ((Data) -> Void)?
    var onStopped: ((_ reason: String, _ message: String) -> Void)?

    private var timer: DispatchSourceTimer?
    private var phase: Float = 0
    private let queue = DispatchQueue(label: "mock-audio")

    private let sampleApps: [AppEntry] = [
        AppEntry(pid: 4001, bundleId: "us.zoom.xos", name: "Zoom"),
        AppEntry(pid: 4002, bundleId: "com.microsoft.teams2", name: "Microsoft Teams"),
        AppEntry(pid: 4003, bundleId: "com.apple.Music", name: "Music"),
        AppEntry(pid: 4004, bundleId: "com.google.Chrome", name: "Google Chrome"),
    ]

    func preflightPermission() -> Bool { true }
    func requestPermission() {}

    func verifyEffectivePermission(_ completion: @escaping (Bool) -> Void) {
        completion(true)
    }

    func listApplications(completion: @escaping ([AppEntry]) -> Void) {
        completion(sampleApps)
    }

    func start(mode: CaptureMode, completion: @escaping (Result<AudioFormat, Error>) -> Void) {
        queue.async { [self] in
            stopTimerLocked()
            isCapturing = true
            let t = DispatchSource.makeTimerSource(queue: queue)
            t.schedule(deadline: .now() + 0.1, repeating: 0.1)
            t.setEventHandler { [self] in emitFrame() }
            t.resume()
            timer = t
            completion(.success(.wire))
        }
    }

    func stop(completion: (() -> Void)?) {
        queue.async { [self] in
            stopTimerLocked()
            isCapturing = false
            completion?()
        }
    }

    private func stopTimerLocked() {
        timer?.cancel()
        timer = nil
    }

    private func emitFrame() {
        // 440Hz @16k，振幅 0.3，100ms/帧。
        let samplesPerFrame = 1600
        var samples = [Float](repeating: 0, count: samplesPerFrame)
        let step: Float = 2 * Float.pi * 440 / 16000
        for i in 0..<samplesPerFrame {
            samples[i] = 0.3 * sin(phase)
            phase += step
            if phase > 2 * Float.pi { phase -= 2 * Float.pi }
        }
        var frame = Data(capacity: samplesPerFrame * 4)
        samples.withUnsafeBufferPointer { ptr in
            frame.append(contentsOf: UnsafeRawBufferPointer(ptr))
        }
        onFrame?(frame)
    }
}
