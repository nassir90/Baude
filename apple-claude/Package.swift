// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "apple-claude",
    platforms: [.macOS("26.0")],
    products: [.executable(name: "apple-claude", targets: ["apple-claude"])],
    targets: [.executableTarget(name: "apple-claude", path: "Sources/apple-claude")]
)
