// Baude's notifier on macOS: posts Baude's and Record's notifications through Notification Center as an app of their
// own (its name and logo on the banner, an Open button, a Bot's picture attached), where osascript's are credited to
// Script Editor. Built by baudelib/notifier.py and signed by tools/macos-sign.sh.
//
// Run with --serve by the app it posts for, it reads one JSON object a line on stdin and writes one a line on stdout:
//   in:  {"op": "post", "key", "title", "body", "open_label", "picture"?: a PNG's path, "app"?: a bundle id,
//         "args"?: [what that app's started with to open it]}
//        {"op": "withdraw", "key"}
//   out: {"event": "ready"} or {"event": "refused", "error"} once, as Notification Center answers;
//        {"event": "failed", "key", "error"}; {"event": "clicked", "key"}
// It quits when stdin closes. Started by macOS for a click on a notification whose app isn't serving it any more, it
// opens that app (its "app", with its "args") and quits.
import AppKit
import UserNotifications

let center = UNUserNotificationCenter.current()
let serving = CommandLine.arguments.contains("--serve")
let parent = getppid()

func emit(_ obj: [String: Any]) {
    DispatchQueue.main.async {
        guard let data = try? JSONSerialization.data(withJSONObject: obj) else { return }
        FileHandle.standardOutput.write(data + Data("\n".utf8))
    }
}

final class Notifier: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    var labels = [String: String]()

    func applicationDidFinishLaunching(_ note: Notification) {
        center.delegate = self
        guard serving else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 10) { NSApp.terminate(nil) }
            return
        }
        center.requestAuthorization(options: [.alert, .sound]) { ok, err in
            DispatchQueue.main.async {
                emit(ok ? ["event": "ready"]
                        : ["event": "refused", "error": err.map { "\($0)" } ?? "notifications are off for this app"])
            }
        }
        Thread.detachNewThread {
            while let line = readLine(strippingNewline: true) {
                guard let data = line.data(using: .utf8),
                      let msg = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
                DispatchQueue.main.async { self.handle(msg) }
            }
            DispatchQueue.main.async { NSApp.terminate(nil) }
        }
    }

    func handle(_ msg: [String: Any]) {
        guard let key = msg["key"] as? String else { return }
        switch msg["op"] as? String {
        case "post": post(key, msg)
        case "withdraw":
            center.removePendingNotificationRequests(withIdentifiers: [key])
            center.removeDeliveredNotifications(withIdentifiers: [key])
        default: break
        }
    }

    func category(_ label: String) -> String {
        // (one category for each Open button's wording: the button's label is the category's)
        let id = "open:" + label
        if labels[id] == nil {
            labels[id] = label
            center.setNotificationCategories(Set(labels.map { cid, title in
                UNNotificationCategory(identifier: cid, actions: [
                    UNNotificationAction(identifier: "open", title: title, options: [.foreground])
                ], intentIdentifiers: [])
            }))
        }
        return id
    }

    func post(_ key: String, _ msg: [String: Any]) {
        let content = UNMutableNotificationContent()
        content.title = msg["title"] as? String ?? ""
        content.body = msg["body"] as? String ?? ""
        content.threadIdentifier = key
        content.categoryIdentifier = category(msg["open_label"] as? String ?? "Open")
        var info: [String: Any] = ["key": key, "pid": Int(parent)]
        if let app = msg["app"] as? String { info["app"] = app }
        if let args = msg["args"] as? [String] { info["args"] = args }
        content.userInfo = info
        if let picture = msg["picture"] as? String, !picture.isEmpty {
            // (Notification Center moves an attachment's file into its own store: a copy of it goes)
            let copy = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".png")
            do {
                try FileManager.default.copyItem(at: URL(fileURLWithPath: picture), to: copy)
                content.attachments = [try UNNotificationAttachment(identifier: "picture", url: copy)]
            } catch {
                try? FileManager.default.removeItem(at: copy)
            }
        }
        center.add(UNNotificationRequest(identifier: key, content: content, trigger: nil)) { err in
            if let err = err { emit(["event": "failed", "key": key, "error": "\(err)"]) }
        }
    }

    func userNotificationCenter(_ c: UNUserNotificationCenter, willPresent n: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .list])
    }

    func userNotificationCenter(_ c: UNUserNotificationCenter, didReceive r: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        defer { done() }
        guard r.actionIdentifier == "open" || r.actionIdentifier == UNNotificationDefaultActionIdentifier else { return }
        let info = r.notification.request.content.userInfo
        let key = info["key"] as? String ?? r.notification.request.identifier
        if serving && (info["pid"] as? Int) == Int(parent) {
            NSRunningApplication(processIdentifier: parent)?.activate()  // (the app in front, not this one)
            emit(["event": "clicked", "key": key])
            return
        }
        if let app = info["app"] as? String {
            let open = Process()
            open.executableURL = URL(fileURLWithPath: "/usr/bin/open")
            let args = info["args"] as? [String] ?? []
            open.arguments = ["-b", app] + (args.isEmpty ? [] : ["--args"] + args)
            try? open.run()
        }
        if !serving { DispatchQueue.main.asyncAfter(deadline: .now() + 1) { NSApp.terminate(nil) } }
    }
}

let app = NSApplication.shared
let notifier = Notifier()
app.delegate = notifier
app.setActivationPolicy(.accessory)
app.run()
