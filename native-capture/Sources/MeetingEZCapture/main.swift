import AppKit
import Foundation

// MeetingEZ Capture：macOS 本地音频采集器。
// 常驻菜单栏（LSUIElement），在 127.0.0.1:17642 提供 WebSocket 服务，
// 供 meetingEZ 实时页采集其他应用 / 整个系统的音频输出。
// 详见 native-capture/README.md。

struct ServeOptions {
    var port: UInt16 = defaultPort
    var extraOrigins: [String] = []
    var noGui = false
    var mockAudio = false

    static let usage = """
        用法: meetingez-capture [选项]

        选项:
          --port <N>           WebSocket 端口（默认 17642）
          --allow-origin <值>  追加允许的 Origin（可多次；"*" 全放行，仅限联调）
          --no-gui             不启动菜单栏，纯服务模式
          --mock-audio         使用 440Hz 假音频源（Web 端无权限联调用）
          -h, --help           显示本帮助

        配置文件 ~/.meetingez-capture.json 提供同名默认值（port/allowOrigins/
        mockAudio/noGui），命令行参数可覆盖、--allow-origin 为并集。
        授权后系统"退出并重新打开"、开机自启等无法携带命令行参数的启动路径
        都依赖它保留白名单。隐藏子命令: ws-probe（ws-probe --help 看用法）。
        """

    /// ~/.meetingez-capture.json 提供的默认值；CLI 参数可覆盖。
    static let configFilePath = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".meetingez-capture.json")

    static func loadConfigDefaults() -> ServeOptions {
        var options = ServeOptions()
        guard let data = try? Data(contentsOf: configFilePath),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return options
        }
        if let port = object["port"] as? Int, (1...65535).contains(port) {
            options.port = UInt16(port)
        }
        if let origins = object["allowOrigins"] as? [String] {
            options.extraOrigins.append(contentsOf: origins.filter { !$0.isEmpty })
        }
        if object["mockAudio"] as? Bool == true {
            options.mockAudio = true
        }
        if object["noGui"] as? Bool == true {
            options.noGui = true
        }
        return options
    }

    /// 解析失败时打印用法并返回 nil。
    static func parse(_ args: [String], defaults: ServeOptions = ServeOptions.loadConfigDefaults()) -> ServeOptions? {
        var options = defaults
        var i = 0
        while i < args.count {
            let arg = args[i]
            func value() -> String? {
                guard i + 1 < args.count else { return nil }
                i += 1
                return args[i]
            }
            switch arg {
            case "--port":
                guard let raw = value(), let port = UInt16(raw) else { return fail(arg) }
                options.port = port
            case "--allow-origin":
                guard let origin = value(), !origin.isEmpty else { return fail(arg) }
                options.extraOrigins.append(origin)
            case "--no-gui":
                options.noGui = true
            case "--mock-audio":
                options.mockAudio = true
            case "-h", "--help":
                print(usage)
                exit(0)
            default:
                return fail(arg)
            }
            i += 1
        }
        return options
    }

    private static func fail(_ arg: String) -> ServeOptions? {
        FileHandle.standardError.write(Data("未知参数: \(arg)\n\(usage)\n".utf8))
        return nil
    }
}

let rawArguments = Array(CommandLine.arguments.dropFirst())

// 隐藏子命令：协议层测试客户端。
if rawArguments.first == "ws-probe" {
    exit(WsProbe.run(Array(rawArguments.dropFirst())))
}

guard let serveOptions = ServeOptions.parse(rawArguments) else {
    exit(2)
}

func buildCollector(_ options: ServeOptions) -> (server: CaptureServer, service: CollectorService)? {
    let source: CaptureSource = options.mockAudio ? MockAudioSource() : SystemAudioTap()
    let server = CaptureServer(port: options.port,
                               originPolicy: OriginPolicy(extraAllowed: options.extraOrigins))
    do {
        try server.start()
    } catch {
        let message = (error as? CollectorError)?.message ?? error.localizedDescription
        FileHandle.standardError.write(Data("启动 WebSocket 服务失败: \(message)\n".utf8))
        return nil
    }
    let service = CollectorService(server: server, source: source)
    service.run()
    print("listening on ws://127.0.0.1:\(options.port)\(options.mockAudio ? " (mock-audio)" : "")")
    return (server, service)
}

if serveOptions.noGui {
    // 纯服务模式：无 AppKit，用于自动化验证。
    guard let collector = buildCollector(serveOptions) else {
        exit(1)
    }
    signal(SIGINT) { _ in exit(0) }
    signal(SIGTERM) { _ in exit(0) }
    withExtendedLifetime(collector) {
        dispatchMain()
    }
} else {
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    let delegate = AppDelegate(options: serveOptions)
    app.delegate = delegate
    withExtendedLifetime(delegate) {
        app.run()
    }
    exit(0)
}
