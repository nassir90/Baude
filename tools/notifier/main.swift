// Baude's notifier on macOS: posts Baude's and Record's notifications through Notification Center as an app of their
// own (its name and logo on the banner, an Open button, a Bot's picture attached), where osascript's are credited to
// Script Editor. Built by baudelib/notifier.py and signed by tools/macos-sign.sh.
//
// Run with --serve by the app it posts for, it reads one JSON object a line on stdin and writes one a line on stdout:
//   in:  {"op": "post", "key", "title", "body", "open_label", "picture"?: a PNG's path, "app"?: a bundle id,
//         "args"?: [what that app's started with to open it]}
//        {"op": "withdraw", "key"}
//   out: {"event": "ready"} or {"event": "refused", "error"} as Notification Center answers, and again whenever that
//        changes (checked each time the app it serves comes to the front);
//        {"event": "posted", "key"}; {"event": "failed", "key", "error"};
//        {"event": "clicked", "key", "pid", "args"?}: a click on one of this app's notifications, whichever process
//        posted it ("pid"), with what starts the app on its chat ("args": the app opens it from them when it didn't
//        post it, or posted it before a restart)
// It quits when stdin closes. Started by macOS for a click on a notification while nothing serves it, it opens that
// app (its "app", with its "args") and quits.
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
    // (each Open button's wording, kept for the next launch: a category's registered before a click can reach it)
    var labels = Set(UserDefaults.standard.stringArray(forKey: "labels") ?? [])
    var allowed: Bool?

    func applicationWillFinishLaunching(_ note: Notification) {
        center.delegate = self  // (before launching ends: a click that launched this is delivered then)
        registerCategories()
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        guard serving else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 10) { NSApp.terminate(nil) }
            return
        }
        center.requestAuthorization(options: [.alert, .sound]) { ok, err in
            DispatchQueue.main.async { self.answered(ok, err.map { "\($0)" }) }
        }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { note in
            let front = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
            guard front?.processIdentifier == parent else { return }
            self.recheck()
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

    func answered(_ ok: Bool, _ error: String?) {
        guard allowed != ok else { return }
        allowed = ok
        emit(ok ? ["event": "ready"] : ["event": "refused", "error": error ?? "notifications are off for this app"])
    }

    func recheck() {
        // (turned on or off in System Settings → Notifications since: said as it's seen)
        center.getNotificationSettings { s in
            let ok = s.authorizationStatus == .authorized || s.authorizationStatus == .provisional
            DispatchQueue.main.async { self.answered(ok, nil) }
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

    func registerCategories() {
        // (one category for each Open button's wording: the button's label is the category's)
        center.setNotificationCategories(Set(labels.map { label in
            UNNotificationCategory(identifier: "open:" + label, actions: [
                UNNotificationAction(identifier: "open", title: label, options: [.foreground])
            ], intentIdentifiers: [])
        }))
    }

    func category(_ label: String) -> String {
        if !labels.contains(label) {
            labels.insert(label)
            UserDefaults.standard.set(Array(labels).sorted(), forKey: "labels")
            registerCategories()
        }
        return "open:" + label
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
            emit(err.map { ["event": "failed", "key": key, "error": "\($0)"] } ?? ["event": "posted", "key": key])
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
        if serving {  // (the app it serves opens it, whichever process posted it: open -b only brings it up)
            NSRunningApplication(processIdentifier: parent)?.activate()  // (the app in front, not this one)
            var said: [String: Any] = ["event": "clicked", "key": key, "pid": info["pid"] as? Int ?? 0]
            if let args = info["args"] as? [String] { said["args"] = args }
            emit(said)
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
