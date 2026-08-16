// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "MeetingEZCapture",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "meetingez-capture", targets: ["MeetingEZCapture"])
    ],
    targets: [
        .executableTarget(
            name: "MeetingEZCapture",
            path: "Sources/MeetingEZCapture"
        )
    ]
)
