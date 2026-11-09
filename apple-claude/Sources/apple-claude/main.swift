import Foundation
import FoundationModels

// apple-claude: stands in for `claude -p --input-format stream-json --output-format stream-json ...` for Baude,
// answering with Apple's on-device model (FoundationModels). One JSON object per line on stdin and stdout.
// It takes the flags Baude passes and ignores all of them except --resume (the session id it reports) and
// --permission-mode (bypassPermissions skips the permission question).

let modelName = "apple-foundation-model"
let args = CommandLine.arguments
func flag(_ name: String) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}
let cwd = FileManager.default.currentDirectoryPath
let sessionID = flag("--resume") ?? UUID().uuidString.lowercased()
func newID(_ prefix: String) -> String {
    prefix + String(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased().prefix(24))
}

// MARK: - Output

let outLock = NSLock()
func emit(_ object: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.withoutEscapingSlashes]) else { return }
    outLock.lock()
    defer { outLock.unlock() }
    print(String(decoding: data, as: UTF8.self))
    fflush(stdout)
}

func streamEvent(_ event: [String: Any]) {
    emit(["type": "stream_event", "event": event, "parent_tool_use_id": NSNull(), "session_id": sessionID])
}

func assistant(_ id: String, _ content: [[String: Any]]) {
    emit(["type": "assistant", "parent_tool_use_id": NSNull(), "session_id": sessionID,
          "message": ["id": id, "model": modelName, "role": "assistant", "content": content] as [String: Any]])
}

/// What `full` holds beyond `base` (all of it if it doesn't start with `base`).
func tail(_ full: String, after base: String) -> String {
    guard !base.isEmpty, full.utf8.starts(with: base.utf8) else { return full }
    return String(decoding: Array(full.utf8.dropFirst(base.utf8.count)), as: UTF8.self)
}

// MARK: - One turn's messages

/// Turns the model's growing text into Claude's messages: a message_start, the new pieces as deltas, then the whole
/// text as an assistant message. A tool call ends the message; text after it is a new one.
final class Turn: @unchecked Sendable {
    private let lock = NSLock()
    private var mid: String?
    private var sent = ""      // the open message's text, as sent
    private var seen = ""      // the stream's latest text
    private var base = ""      // what the stream held before the open message (if it keeps earlier text)
    private(set) var last = "" // the last finished message's text
    private(set) var messages = 0
    private var commands = 0

    /// Another command in this turn: false once there are too many.
    func allowCommand(limit: Int) -> Bool {
        lock.lock(); defer { lock.unlock() }
        commands += 1
        return commands <= limit
    }

    func feed(_ content: String) {
        lock.lock(); defer { lock.unlock() }
        seen = content
        let text = tail(content, after: base)
        if mid == nil {
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
            mid = newID("msg_")
            streamEvent(["type": "message_start", "message": ["id": mid!]])
            streamEvent(["type": "content_block_start", "index": 0, "content_block": ["type": "text", "text": ""]])
        }
        guard text.utf8.count > sent.utf8.count, text.utf8.starts(with: sent.utf8) else { return }
        let piece = String(decoding: Array(text.utf8.dropFirst(sent.utf8.count)), as: UTF8.self)
        sent = text
        streamEvent(["type": "content_block_delta", "index": 0, "delta": ["type": "text_delta", "text": piece]])
    }

    /// The open message is complete. `toolCall`: the stream goes on after it, so what it held so far isn't new.
    func finish(toolCall: Bool = false) {
        lock.lock(); defer { lock.unlock() }
        if let id = mid {
            assistant(id, [["type": "text", "text": sent]])
            last = sent
            messages += 1
        }
        mid = nil
        sent = ""
        if toolCall { base = seen }
    }

    /// A message of our own (an error), after whatever was said.
    func say(_ text: String) {
        finish()
        let id = newID("msg_")
        streamEvent(["type": "message_start", "message": ["id": id]])
        streamEvent(["type": "content_block_start", "index": 0, "content_block": ["type": "text", "text": ""]])
        streamEvent(["type": "content_block_delta", "index": 0, "delta": ["type": "text_delta", "text": text]])
        assistant(id, [["type": "text", "text": text]])
        lock.lock(); last = text; messages += 1; lock.unlock()
    }
}

// MARK: - The agent

enum Availability {
    /// nil when the model can answer, else why not.
    static var problem: String? {
        switch SystemLanguageModel.default.availability {
        case .available: return nil
        case .unavailable(.deviceNotEligible): return "This Mac can't run Apple Intelligence."
        case .unavailable(.appleIntelligenceNotEnabled): return "Turn on Apple Intelligence in System Settings."
        case .unavailable(.modelNotReady): return "The on-device model is still downloading. Try again shortly."
        case .unavailable: return "The on-device model isn't available."
        }
    }
}

struct Failure: Error {
    let text: String
    init(_ text: String) { self.text = text }
}

final class Agent: @unchecked Sendable {
    private let lock = NSLock()
    private var session: LanguageModelSession?
    private var turn: Turn?
    private var task: Task<Void, Never>?
    private var mode = "default"
    private var waiting: [String: CheckedContinuation<[String: Any], Never>] = [:]
    private var running: Process?
    private var cancelled = false
    private let prompts: AsyncStream<String>
    private let promptsIn: AsyncStream<String>.Continuation

    static let maxCommands = 8
    static let instructions = """
        You are a helpful assistant working in the user's project folder (\(cwd)). You can run shell commands there \
        with the Exec tool. Run a command when you need to look at files or the system, then say briefly what you found. \
        If the user only chats or asks a question, just answer.
        """

    init() {
        (prompts, promptsIn) = AsyncStream.makeStream(of: String.self)
        if let m = flag("--permission-mode") { mode = m == "manual" ? "default" : m }
    }

    // MARK: stdin (on its own thread)

    func handle(_ msg: [String: Any]) {
        switch msg["type"] as? String {
        case "control_request":
            let request = msg["request"] as? [String: Any] ?? [:]
            let subtype = request["subtype"] as? String
            var response: [String: Any] = [:]
            switch subtype {
            case "initialize":
                response = ["commands": [Any](),
                            "models": [["value": "apple", "resolvedModel": modelName, "displayName": "Apple Foundation Model"]],
                            "current_permission_mode": mode]
            case "get_settings": response = ["applied": ["model": "apple", "effort": NSNull()] as [String: Any]]
            case "get_context_usage": response = ["categories": [Any](), "totalTokens": 0, "maxTokens": 4096, "percentage": 0]
            case "set_permission_mode":
                lock.lock(); mode = request["mode"] as? String ?? mode; lock.unlock()
            default: break
            }
            emit(["type": "control_response", "response": ["subtype": "success", "request_id": msg["request_id"] ?? "",
                                                           "response": response] as [String: Any]])
            if subtype == "initialize" {
                emit(["type": "system", "subtype": "init", "session_id": sessionID, "cwd": cwd, "model": modelName,
                      "tools": ["Exec"], "permissionMode": mode])
            } else if subtype == "set_permission_mode" {
                emit(["type": "system", "subtype": "status", "status": NSNull(), "permissionMode": mode, "session_id": sessionID])
            } else if subtype == "interrupt" {
                interrupt()
            }
        case "control_response":
            let r = msg["response"] as? [String: Any] ?? [:]
            guard let rid = r["request_id"] as? String else { return }
            let answer = r["subtype"] as? String == "success" ? (r["response"] as? [String: Any] ?? [:]) : [:]
            resolve(rid, answer.isEmpty ? ["behavior": "deny", "message": "Permission denied by user"] : answer)
        case "user":
            promptsIn.yield(Agent.text(of: (msg["message"] as? [String: Any])?["content"]))
        default: break
        }
    }

    static func text(of content: Any?) -> String {
        if let s = content as? String { return s }
        var parts: [String] = []
        for block in content as? [[String: Any]] ?? [] {
            if block["type"] as? String == "text" { parts.append(block["text"] as? String ?? "") }
            else { parts.append("[an attachment the on-device model can't read]") }
        }
        return parts.joined(separator: "\n")
    }

    // MARK: turns

    /// Runs the user's messages one after another.
    func serve() async {
        for await prompt in prompts {
            let t = Task { await self.runTurn(prompt) }
            lock.withLock { task = t }
            await t.value
            lock.withLock { task = nil }
        }
    }

    func interrupt() {
        lock.lock()
        let t = task
        let ids = Array(waiting.keys)
        lock.unlock()
        t?.cancel()
        for id in ids { resolve(id, ["behavior": "deny", "message": "Interrupted by the user"]) }
        terminateRunning()
    }

    func runTurn(_ prompt: String) async {
        let start = Date()
        let turn = Turn()
        lock.withLock { self.turn = turn }
        var failed = false
        do {
            if let problem = Availability.problem { throw Failure(problem) }
            let session = currentSession()
            for try await snapshot in session.streamResponse(to: prompt) {
                turn.feed(snapshot.content)
            }
            turn.finish()
        } catch is CancellationError {
            turn.finish()
        } catch {
            turn.finish()
            failed = true
            turn.say(message(for: error))
        }
        lock.withLock { self.turn = nil }
        emit(["type": "result", "subtype": "success", "is_error": failed, "session_id": sessionID, "result": turn.last,
              "num_turns": max(1, turn.messages), "duration_ms": Int(Date().timeIntervalSince(start) * 1000),
              "total_cost_usd": 0, "usage": ["input_tokens": 0, "output_tokens": 0]])
    }

    func message(for error: Error) -> String {
        if let f = error as? Failure { return f.text }
        if let e = error as? LanguageModelSession.GenerationError {
            switch e {
            case .exceededContextWindowSize:
                lock.lock(); session = nil; lock.unlock()
                return "This chat has filled the on-device model's context window. I've started a fresh session, so it won't remember the earlier messages."
            case .guardrailViolation: return "Apple's on-device model declined that request."
            case .assetsUnavailable: return Availability.problem ?? "The on-device model's assets aren't available."
            default: break
            }
        }
        return error.localizedDescription
    }

    func currentSession() -> LanguageModelSession {
        lock.lock(); defer { lock.unlock() }
        if let session { return session }
        let s = LanguageModelSession(tools: [ExecTool(agent: self)], instructions: Agent.instructions)
        session = s
        return s
    }

    // MARK: Exec

    /// The Exec tool: show the call, ask Baude for permission, run it, show the result, and give the output to the model.
    func exec(_ command: String) async -> String {
        let (turn, bypass) = lock.withLock { (self.turn, mode == "bypassPermissions") }
        turn?.finish(toolCall: true)
        let tid = newID("toolu_")
        let input: [String: Any] = ["command": command]
        assistant(newID("msg_"), [["type": "tool_use", "id": tid, "name": "Exec", "input": input]])

        var output = "Permission denied by user"
        var isError = true
        if let turn, !turn.allowCommand(limit: Agent.maxCommands) {
            output = "Too many commands in one turn. Answer with what you have."
        } else {
            let answer = bypass ? ["behavior": "allow"] : await ask(tool: "Exec", input: input, toolUseID: tid)
            if answer["behavior"] as? String == "allow" {
                (output, isError) = await shell(command)
            } else if let why = answer["message"] as? String, !why.isEmpty {
                output = why
            }
        }
        emit(["type": "user", "session_id": sessionID, "message": ["role": "user", "content": [
            ["type": "tool_result", "tool_use_id": tid, "is_error": isError, "content": output] as [String: Any]]] as [String: Any]])
        return output
    }

    func ask(tool: String, input: [String: Any], toolUseID: String) async -> [String: Any] {
        let rid = newID("perm_")
        let denied: [String: Any] = ["behavior": "deny", "message": "Interrupted by the user"]
        return await withTaskCancellationHandler {
            await withCheckedContinuation { (cont: CheckedContinuation<[String: Any], Never>) in
                lock.lock(); waiting[rid] = cont; lock.unlock()
                emit(["type": "control_request", "request_id": rid, "request": [
                    "subtype": "can_use_tool", "tool_name": tool, "input": input, "tool_use_id": toolUseID] as [String: Any]])
                if Task.isCancelled { resolve(rid, denied) }
            }
        } onCancel: {
            resolve(rid, denied)
        }
    }

    func resolve(_ rid: String, _ answer: [String: Any]) {
        lock.lock()
        let cont = waiting.removeValue(forKey: rid)
        lock.unlock()
        cont?.resume(returning: answer)
    }

    /// `/bin/zsh -lc command` in the folder: stdout and stderr together, cut short for the model's small context.
    func shell(_ command: String) async -> (String, Bool) {
        lock.withLock { cancelled = false }
        return await withTaskCancellationHandler {
            await withCheckedContinuation { (cont: CheckedContinuation<(String, Bool), Never>) in
                DispatchQueue.global().async {
                    let p = Process()
                    p.executableURL = URL(fileURLWithPath: "/bin/zsh")
                    p.arguments = ["-lc", command]
                    p.currentDirectoryURL = URL(fileURLWithPath: cwd)
                    let pipe = Pipe()
                    p.standardOutput = pipe
                    p.standardError = pipe
                    p.standardInput = FileHandle.nullDevice  // (stdin is the protocol's)
                    do { try p.run() } catch {
                        cont.resume(returning: ("Could not run the command: \(error.localizedDescription)", true))
                        return
                    }
                    self.lock.lock()
                    self.running = p
                    let stop = self.cancelled
                    self.lock.unlock()
                    if stop { p.terminate() }
                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    p.waitUntilExit()
                    self.lock.lock(); self.running = nil; self.lock.unlock()
                    var text = String(decoding: data, as: UTF8.self)
                    if text.count > 4000 { text = String(text.prefix(4000)) + "\n… (\(text.count - 4000) more characters cut)" }
                    let code = p.terminationStatus
                    if code != 0 { text += (text.isEmpty || text.hasSuffix("\n") ? "" : "\n") + "(exit status \(code))" }
                    cont.resume(returning: (text.isEmpty ? "(no output)" : text, code != 0))
                }
            }
        } onCancel: {
            terminateRunning()
        }
    }

    func terminateRunning() {
        lock.lock()
        cancelled = true
        let p = running
        lock.unlock()
        p?.terminate()
    }
}

/// The one tool the model has: a shell command.
struct ExecTool: Tool {
    typealias Arguments = GeneratedContent
    typealias Output = String

    let agent: Agent
    let name = "Exec"
    let description = "Run a shell command in the project folder and get its output."
    let parameters = try! GenerationSchema(
        root: DynamicGenerationSchema(name: "Exec", description: nil, properties: [
            DynamicGenerationSchema.Property(name: "command", description: "The shell command to run.",
                                             schema: DynamicGenerationSchema(type: String.self), isOptional: false)]),
        dependencies: [])

    func call(arguments: GeneratedContent) async throws -> String {
        let command = (try? arguments.value(String.self, forProperty: "command")) ?? ""
        return await agent.exec(command)
    }
}

// MARK: - Main

signal(SIGPIPE, SIG_IGN)
let agent = Agent()

// stdin is read on its own thread: permission answers arrive while a turn is waiting for them.
Thread.detachNewThread {
    while let line = readLine(strippingNewline: true) {
        guard let data = line.data(using: .utf8),
              let msg = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
        agent.handle(msg)
    }
    agent.terminateRunning()
    exit(0)
}

Task { await agent.serve() }
dispatchMain()
