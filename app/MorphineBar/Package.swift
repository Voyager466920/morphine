// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "MorphineBar",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "MorphineBar", path: "Sources/MorphineBar")
    ]
)
