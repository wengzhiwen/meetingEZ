import Darwin
import Foundation

/// 隐藏子命令 `ws-probe`：对本机采集器做协议层自动化验证的 WebSocket 客户端。
///
/// 说明：这里不用 Network.framework 的 NWProtocolWebSocket 客户端——在部分
/// macOS 版本上裸 CLI 二进制的 NW WS 客户端会在握手前直接 abort（实测
/// macOS 26.5，TCP 正常、服务端正常）。BSD socket + 手写握手是确定性的。
/// 服务端仍用 NWProtocolWebSocket（与浏览器互操作已验证）。
///
///   meetingez-capture ws-probe ping [--url ...] [--origin O]
///   meetingez-capture ws-probe list-apps
///   meetingez-capture ws-probe request-permission
///   meetingez-capture ws-probe start-system [--duration 8] [--no-read]
///   meetingez-capture ws-probe start-apps --bundle-ids a,b [--duration 8]
enum WsProbe {
    struct Options {
        var url = "ws://127.0.0.1:\(defaultPort)"
        var origin: String?
        var duration: Double = 8
        var bundleIds: [String] = []
        var noRead = false
        var verbose = false
    }

    static let usage = """
        用法: meetingez-capture ws-probe <命令> [选项]

        命令:
          ping                 握手 + ping/pong 往返
          list-apps            拉取可采集应用列表
          request-permission   触发系统授权弹窗并等待结果
          start-system         采集整个系统音频并统计帧
          start-apps           采集指定应用音频并统计帧（需 --bundle-ids）

        选项:
          --url ws://127.0.0.1:17642
          --origin <value>     握手时携带的 Origin 头
          --duration <秒>      采集持续时间（默认 8）
          --bundle-ids a,b     apps 模式的 bundleId 列表
          --no-read            暂停读取以验证服务端背压丢帧
          -v, --verbose        打印全部收发消息
        """

    // ---- 最小 WebSocket 客户端（同步阻塞） ----

    struct Frame {
        enum Kind {
            case text(String)
            case binary(Data)
            case close
            case ping
            case pong
        }
        var kind: Kind
    }

    final class ProbeSocket {
        private var fd: Int32 = -1
        private let host: String
        private let port: UInt16
        var verbose = false

        init(url: String) throws {
            guard let (host, port) = Self.parseWebSocketURL(url) else {
                throw ProbeError("无法解析 URL: \(url)")
            }
            self.host = host
            self.port = port
        }

        struct ProbeError: Error, CustomStringConvertible {
            let description: String
            init(_ message: String) { self.description = message }
        }

        func connect(timeout: Double, origin: String?) throws {
            fd = socket(AF_INET, SOCK_STREAM, 0)
            guard fd >= 0 else { throw ProbeError("socket() 失败") }
            var yes: Int32 = 1
            setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, socklen_t(MemoryLayout<Int32>.size))

            var address = sockaddr_in()
            address.sin_family = sa_family_t(AF_INET)
            address.sin_port = port.bigEndian
            guard inet_pton(AF_INET, host, &address.sin_addr) == 1 else {
                Darwin.close(fd); fd = -1
                throw ProbeError("仅支持 IPv4 地址字面量: \(host)")
            }

            // 非阻塞 connect + 超时
            let flags = fcntl(fd, F_GETFL, 0)
            _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)
            let connectResult = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                    Darwin.connect(fd, sa, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            if connectResult != 0 && errno != EINPROGRESS {
                Darwin.close(fd); fd = -1
                throw ProbeError(String(cString: strerror(errno)))
            }
            if connectResult != 0 {
                var pollFd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
                let polled = poll(&pollFd, 1, Int32(timeout * 1000))
                if polled <= 0 {
                    Darwin.close(fd); fd = -1
                    throw ProbeError("连接超时")
                }
            }
            _ = fcntl(fd, F_SETFL, flags) // 恢复阻塞模式

            // TCP 层已通，接下来读写在带超时的阻塞模式下进行。
            try handshake(origin: origin)
        }

        private func handshake(origin: String?) throws {
            var keyBytes = [UInt8](repeating: 0, count: 16)
            _ = SecRandomCopyBytes(kSecRandomDefault, 16, &keyBytes)
            let key = Data(keyBytes).base64EncodedString()

            var request = "GET / HTTP/1.1\r\n"
            request += "Host: \(host):\(port)\r\n"
            request += "Upgrade: websocket\r\n"
            request += "Connection: Upgrade\r\n"
            request += "Sec-WebSocket-Key: \(key)\r\n"
            request += "Sec-WebSocket-Version: 13\r\n"
            if let origin {
                request += "Origin: \(origin)\r\n"
            }
            request += "\r\n"
            try sendRaw(Data(request.utf8))

            // 读取到头部结束。
            var buffer = Data()
            let deadline = Date().addingTimeInterval(5)
            while buffer.range(of: Data("\r\n\r\n".utf8)) == nil {
                if Date() > deadline { throw ProbeError("握手响应超时") }
                let chunk = try recvRaw(timeout: 5)
                buffer.append(chunk)
            }
            guard let headerEnd = buffer.range(of: Data("\r\n\r\n".utf8)) else {
                throw ProbeError("握手响应不完整")
            }
            let header = String(decoding: buffer[..<headerEnd.lowerBound], as: UTF8.self)
            guard header.hasPrefix("HTTP/1.1 101") || header.hasPrefix("HTTP/1.0 101") else {
                let statusLine = header.split(separator: "\r\n").first.map(String.init) ?? "?"
                throw ProbeError("握手被拒绝: \(statusLine)")
            }
            if verbose {
                print("<< \(header.split(separator: "\r\n").first ?? "")")
            }
            // 101 之后可能紧跟着一个帧，留在残留缓冲里。
            remainder = buffer[headerEnd.upperBound...]
        }

        private var remainder = Data()

        private func sendRaw(_ data: Data) throws {
            try data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                var offset = 0
                while offset < raw.count {
                    let sent = send(fd, raw.baseAddress!.advanced(by: offset), raw.count - offset, 0)
                    if sent <= 0 {
                        throw ProbeError("send 失败: \(String(cString: strerror(errno)))")
                    }
                    offset += sent
                }
            }
        }

        private func recvRaw(timeout: Double) throws -> Data {
            var pollFd = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
            let polled = poll(&pollFd, 1, Int32(timeout * 1000))
            if polled == 0 { throw ProbeError("recv 超时") }
            if polled < 0 { throw ProbeError("poll 失败") }
            var buffer = [UInt8](repeating: 0, count: 65536)
            let received = recv(fd, &buffer, buffer.count, 0)
            if received <= 0 {
                throw ProbeError("连接已关闭")
            }
            return Data(buffer[0..<received])
        }

        /// 读取一个完整消息（自动拼接分片帧；close 前保证语义完整）。
        /// 空闲 idleTimeout 秒无数据返回 nil。
        func receiveMessage(idleTimeout: Double) throws -> Frame? {
            var acc = Data()
            while true {
                let frame = try receiveFrame(idleTimeout: remainder.isEmpty ? idleTimeout : 5)
                switch frame.opcode {
                case 0x1, 0x2: // text / binary
                    acc.append(frame.payload)
                    if frame.fin {
                        if frame.opcode == 0x1 {
                            return Frame(kind: .text(String(decoding: acc, as: UTF8.self)))
                        }
                        return Frame(kind: .binary(acc))
                    }
                case 0x8:
                    return Frame(kind: .close)
                case 0x9: // ping → pong
                    try sendFrame(opcode: 0xA, payload: frame.payload)
                case 0xA:
                    continue
                default:
                    continue
                }
            }
        }

        private struct RawFrame {
            var fin: Bool
            var opcode: UInt8
            var payload: Data
        }

        private func receiveFrame(idleTimeout: Double) throws -> RawFrame {
            func need(_ count: Int, _ have: Data, timeout: Double) throws -> Data {
                var data = have
                while data.count < count {
                    // 先消耗 remainder，再读 socket。
                    if !remainder.isEmpty {
                        let take = min(remainder.count, count - data.count)
                        data.append(remainder.prefix(take))
                        remainder.removeFirst(take)
                        continue
                    }
                    data.append(try recvRaw(timeout: timeout))
                }
                if data.count > count {
                    remainder.append(contentsOf: data[count...])
                    data = data[..<count]
                }
                return data
            }

            let header = try need(2, Data(), timeout: idleTimeout)
            let first = header[header.startIndex]
            let secondByte = header[header.index(after: header.startIndex)]
            let fin = (first & 0x80) != 0
            let opcode = first & 0x0F
            let masked = (secondByte & 0x80) != 0
            var length = Int(secondByte & 0x7F)
            if length == 126 {
                let ext = try need(2, Data(), timeout: 5)
                length = Int(ext[ext.startIndex]) << 8 | Int(ext[ext.index(after: ext.startIndex)])
            } else if length == 127 {
                let ext = try need(8, Data(), timeout: 5)
                length = 0
                for byte in ext {
                    length = length << 8 | Int(byte)
                }
            }
            var mask = [UInt8]()
            if masked {
                let maskData = try need(4, Data(), timeout: 5)
                mask = Array(maskData)
            }
            var payload = try need(length, Data(), timeout: 5)
            if masked {
                for index in payload.indices {
                    payload[index] ^= mask[payload.distance(from: payload.startIndex, to: index) % 4]
                }
            }
            return RawFrame(fin: fin, opcode: opcode, payload: payload)
        }

        func sendText(_ string: String) throws {
            if verbose {
                print(">> \(string)")
            }
            try sendFrame(opcode: 0x1, payload: Data(string.utf8))
        }

        /// 客户端到服务端的帧必须掩码。
        private func sendFrame(opcode: UInt8, payload: Data) throws {
            var mask = [UInt8](repeating: 0, count: 4)
            _ = SecRandomCopyBytes(kSecRandomDefault, 4, &mask)

            var frame = Data([0x80 | opcode])
            let length = payload.count
            if length < 126 {
                frame.append(UInt8(0x80 | length))
            } else if length <= 0xFFFF {
                frame.append(UInt8(0x80 | 126))
                frame.append(UInt8((length >> 8) & 0xFF))
                frame.append(UInt8(length & 0xFF))
            } else {
                frame.append(UInt8(0x80 | 127))
                for shift in stride(from: 56, through: 0, by: -8) {
                    frame.append(UInt8((length >> shift) & 0xFF))
                }
            }
            frame.append(contentsOf: mask)
            var masked = Data(capacity: length)
            for (index, byte) in payload.enumerated() {
                masked.append(byte ^ mask[index % 4])
            }
            frame.append(masked)
            try sendRaw(frame)
        }

        func close() {
            if fd >= 0 {
                try? sendFrame(opcode: 0x8, payload: Data())
                Darwin.close(fd)
                fd = -1
            }
        }

        static func parseWebSocketURL(_ url: String) -> (String, UInt16)? {
            guard url.lowercased().hasPrefix("ws://") else { return nil }
            let rest = url.dropFirst(5)
            guard let colon = rest.lastIndex(of: ":"), colon != rest.startIndex else {
                return (String(rest), 80)
            }
            let host = String(rest[..<colon])
            let portStr = rest[rest.index(after: colon)...]
            guard let port = UInt16(portStr), port > 0 else { return nil }
            return (host, port)
        }
    }

    // ---- 统计 ----

    final class BinaryStats {
        private(set) var frames = 0
        private(set) var bytes = 0
        private var sumSquares: Double = 0
        private var samples: Double = 0
        private(set) var maxAbs: Float = 0
        private(set) var serverStats: [String: Any]?

        func add(_ data: Data) {
            frames += 1
            bytes += data.count
            data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                let buffer = raw.bindMemory(to: Float32.self)
                for sample in buffer {
                    let v = abs(sample)
                    if v > maxAbs { maxAbs = v }
                    sumSquares += Double(sample) * Double(sample)
                    samples += 1
                }
            }
        }

        var rms: Double {
            samples > 0 ? (sumSquares / samples).squareRoot() : 0
        }

        func observeStats(_ object: [String: Any]) {
            serverStats = object
        }

        var summary: String {
            var parts = [
                "frames=\(frames)",
                "bytes=\(bytes)",
                String(format: "rms=%.4f", rms),
                String(format: "maxAbs=%.4f", maxAbs)
            ]
            if let serverStats {
                parts.append("serverStats=\(jsonLine(serverStats))")
            }
            return parts.joined(separator: " ")
        }
    }

    // ---- 入口 ----

    static func run(_ args: [String]) -> Int32 {
        var options = Options()
        var command: String?

        var i = 0
        while i < args.count {
            let arg = args[i]
            func value() -> String? {
                guard i + 1 < args.count else { return nil }
                i += 1
                return args[i]
            }
            switch arg {
            case "--url":
                guard let v = value() else { return usageError("--url 需要值") }
                options.url = v
            case "--origin":
                guard let v = value() else { return usageError("--origin 需要值") }
                options.origin = v
            case "--duration":
                guard let v = value(), let d = Double(v) else { return usageError("--duration 需要数值") }
                options.duration = d
            case "--bundle-ids":
                guard let v = value() else { return usageError("--bundle-ids 需要值") }
                options.bundleIds = v.split(separator: ",")
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                    .filter { !$0.isEmpty }
            case "--no-read":
                options.noRead = true
            case "-v", "--verbose":
                options.verbose = true
            case "-h", "--help":
                print(usage)
                return 0
            default:
                if arg.hasPrefix("-") {
                    return usageError("未知参数: \(arg)")
                }
                if command != nil {
                    return usageError("只能指定一个命令")
                }
                command = arg
            }
            i += 1
        }

        guard let command else {
            FileHandle.standardError.write(Data((usage + "\n").utf8))
            return 2
        }

        let socket: ProbeSocket
        do {
            socket = try ProbeSocket(url: options.url)
            socket.verbose = options.verbose
            try socket.connect(timeout: 5, origin: options.origin)
        } catch {
            FileHandle.standardError.write(Data("连接失败: \(error)\n".utf8))
            return 1
        }
        defer { socket.close() }

        func fail(_ message: String) -> Int32 {
            FileHandle.standardError.write(Data("\(message)\n".utf8))
            return 1
        }

        // 读取并解析一条文本消息；返回 JSON 或 nil（超时/关闭/二进制跳过）。
        func nextText(timeout: Double) -> [String: Any]? {
            let deadline = Date().addingTimeInterval(timeout)
            while Date() < deadline {
                guard let frame = try? socket.receiveMessage(idleTimeout: deadline.timeIntervalSinceNow) else {
                    return nil
                }
                switch frame.kind {
                case let .text(text):
                    if options.verbose {
                        print("<< \(text)")
                    }
                    let data = Data(text.utf8)
                    if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                        return object
                    }
                case .binary, .ping, .pong:
                    continue
                case .close:
                    return nil
                }
            }
            return nil
        }

        // 等待特定类型的消息；error 消息视为失败。
        func wait(_ type: String, timeout: Double) -> (matched: [String: Any]?, error: [String: Any]?) {
            let deadline = Date().addingTimeInterval(timeout)
            while Date() < deadline {
                guard let object = nextText(timeout: deadline.timeIntervalSinceNow) else {
                    return (nil, nil)
                }
                if object["type"] as? String == "error" {
                    return (nil, object)
                }
                if object["type"] as? String == type {
                    return (object, nil)
                }
            }
            return (nil, nil)
        }

        // 先等 hello（验证握手与 Origin 策略通过）。
        let hello = wait("hello", timeout: 3)
        guard hello.matched != nil else {
            return fail("未收到 hello（连接可能被 Origin 策略拒绝）。\(hello.error.map { jsonLine($0) } ?? "")")
        }

        switch command {
        case "ping":
            do {
                try socket.sendText(jsonLine(["type": "ping", "ref": "probe"]))
            } catch {
                return fail("发送失败: \(error)")
            }
            let pong = wait("pong", timeout: 5)
            guard pong.matched != nil else {
                return fail("未收到 pong。\(pong.error.map { jsonLine($0) } ?? "")")
            }
            print("pong ok")
            return 0

        case "list-apps":
            do {
                try socket.sendText(jsonLine(["type": "listApps", "ref": "probe"]))
            } catch {
                return fail("发送失败: \(error)")
            }
            let result = wait("apps", timeout: 10)
            guard let apps = result.matched else {
                return fail("未收到 apps。\(result.error.map { jsonLine($0) } ?? "")")
            }
            let list = apps["apps"] as? [[String: Any]] ?? []
            print(jsonLine(["count": list.count]))
            for app in list {
                print(jsonLine(app))
            }
            return list.isEmpty ? 1 : 0

        case "request-permission":
            do {
                try socket.sendText(jsonLine(["type": "requestPermission", "ref": "probe"]))
            } catch {
                return fail("发送失败: \(error)")
            }
            // 系统弹窗需要用户交互，给足时间。
            let result = wait("permission", timeout: 300)
            guard let permission = result.matched else {
                return fail("未收到 permission 回复。\(result.error.map { jsonLine($0) } ?? "")")
            }
            print(jsonLine(permission))
            return (permission["granted"] as? Bool) == true ? 0 : 1

        case "start-system", "start-apps":
            if command == "start-apps" && options.bundleIds.isEmpty {
                return usageError("start-apps 需要 --bundle-ids")
            }
            let start: [String: Any] = command == "start-apps"
                ? ["type": "start", "mode": "apps", "bundleIds": options.bundleIds, "ref": "probe"]
                : ["type": "start", "mode": "system", "ref": "probe"]
            do {
                try socket.sendText(jsonLine(start))
            } catch {
                return fail("发送失败: \(error)")
            }
            let started = wait("started", timeout: 20)
            guard let startedMessage = started.matched else {
                return fail("未收到 started。\(started.error.map { jsonLine($0) } ?? "")")
            }
            print("started: \(jsonLine(startedMessage))")

            let stats = BinaryStats()

            if options.noRead {
                // 背压测试：完全不读，让发送端积压触发丢帧。
                Thread.sleep(forTimeInterval: options.duration)
                do {
                    try socket.sendText(jsonLine(["type": "stop", "ref": "probe"]))
                } catch {
                    print("(stop 发送失败，符合背压场景预期: \(error))")
                }
                // 恢复读取后服务端 stats 消息会陆续到达，收一条就撤。
                if let object = nextText(timeout: 3) {
                    if object["type"] as? String == "stats" {
                        stats.observeStats(object)
                    }
                }
                print("no-read stats: \(stats.summary)")
                return 0
            }

            // 采集中：被动接收，直到时长结束。
            let endTime = Date().addingTimeInterval(options.duration)
            while Date() < endTime {
                guard let frame = try? socket.receiveMessage(idleTimeout: min(1, endTime.timeIntervalSinceNow)) else {
                    continue
                }
                switch frame.kind {
                case let .binary(data):
                    stats.add(data)
                case let .text(text):
                    if let object = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] {
                        if object["type"] as? String == "stats" {
                            stats.observeStats(object)
                        } else {
                            print("采集期收到文本: \(text)")
                        }
                    }
                case .close:
                    return fail("采集中连接被关闭。")
                case .ping, .pong:
                    break
                }
            }

            do {
                try socket.sendText(jsonLine(["type": "stop", "ref": "probe"]))
            } catch {
                return fail("发送 stop 失败: \(error)")
            }
            let stopped = wait("stopped", timeout: 15)
            if stopped.matched == nil {
                print("NOTE: 未收到 stopped（stats: \(stats.summary)）")
                return 1
            }
            print(stats.summary)
            return stats.frames > 0 ? 0 : 1

        default:
            return usageError("未知命令: \(command)")
        }
    }

    private static func usageError(_ message: String) -> Int32 {
        FileHandle.standardError.write(Data("\(message)\n\(usage)\n".utf8))
        return 2
    }
}
