package dev.baude.android.screenshots

import dev.baude.android.Msg
import dev.baude.android.TasksState
import org.json.JSONObject

/** A Bot's tasks as its host would list them (made up: no host asked), for the Tasks page drawn by the rigs: three
 * tasks, Deploy site's page with its triggers, inputs and runs, and a run of it that failed and was handed to
 * its Bot, Juniper. Times are from now, on a clock at UTC. */
object TaskFixtures {
    private val now get() = System.currentTimeMillis() / 1000.0

    /** Today at hh:mm (UTC), or days back. */
    private fun at(hh: Int, mm: Int, daysBack: Int = 0): Double {
        val day = Math.floor(now / 86_400) * 86_400 - daysBack * 86_400.0
        return day + hh * 3600 + mm * 60
    }

    const val DEPLOY_SCRIPT = """import subprocess
import baudetask as task

@task.main
def deploy(inputs):
    branch = inputs["branch"]
    task.say(f"deploying {branch}")
    subprocess.run(["git", "checkout", branch], check=True)
    subprocess.run(["make", "publish"], check=True)
    if inputs["notify"]:
        task.say("published")"""

    private val form = listOf(
        Msg.TaskField(name = "force", label = "Rebuild and redeploy even when staging has nothing new", type = "bool", default = false),
        Msg.TaskField(name = "notify", label = "Tell the channel once it's published", type = "bool", default = true),
        Msg.TaskField(name = "branch", label = "Branch", type = "text", default = "main"),
        Msg.TaskField(name = "target", label = "Target", type = "choice", choices = listOf("staging", "local", "preview"), default = "local"))
    private val triggers = listOf(
        Msg.TaskTrigger(id = "g1", kind = "git_push", repo = "~/projects/site", branch = "main", pollS = 60, fills = mapOf("branch" to "branch")),
        Msg.TaskTrigger(id = "s1", kind = "schedule", at = "09:00", days = listOf(0, 1, 2, 3, 4)))

    private fun run(id: String, task: String, name: String, status: String, kind: String, started: Double, seconds: Double?,
                    version: String) =
        Msg.TaskRun(id = id, task = task, name = name, status = status, kind = kind, started = started,
            ended = seconds?.let { started + it }, seconds = seconds, version = version)

    private const val V = "3f9c2a7e41b0"
    private const val V_OLD = "8a21d04c77e3"

    val runs get() = listOf(
        run("r4", "t1", "Deploy site", "done", "git_push", now - 2 * 3600, 48.0, V),
        run("r3", "t1", "Deploy site", "failed", "schedule", at(9, 0, 1), 72.0, V),
        run("r2", "t1", "Deploy site", "done", "manual", at(16, 20, 2), 21.0, V_OLD),
        run("r1", "t1", "Deploy site", "done", "schedule", at(9, 0, 3), 186.0, V_OLD))

    val deploy get() = Msg.Task(id = "t1", name = "Deploy site", runtime = "python", script = DEPLOY_SCRIPT, version = V, form = form,
        triggers = triggers, fallback = true, paused = false, last = runs.first())

    val tasks get() = listOf(
        deploy,
        Msg.Task(id = "t2", name = "Nightly backup", runtime = "shell", version = "c03b91fe2d48", fallback = true, paused = true,
            triggers = listOf(Msg.TaskTrigger(id = "s2", kind = "schedule", at = "02:30", days = (0..6).toList())),
            last = run("b9", "t2", "Nightly backup", "failed", "schedule", at(2, 30), 14.0, "c03b91fe2d48")),
        Msg.Task(id = "t3", name = "Triage issues", runtime = "bot", version = "51e7aa09bd32", fallback = true, paused = false,
            triggers = listOf(Msg.TaskTrigger(id = "s3", kind = "schedule", every = 120)),
            last = run("i5", "t3", "Triage issues", "running", "schedule", now - 40, null, "51e7aa09bd32")))

    private fun line(kind: String, text: String, error: Boolean? = null, values: JSONObject? = null) =
        Msg.TaskLogLine(at = now, kind = kind, text = text, error = error, values = values)

    /** Run r3's log: output, an error, and the failure handed to Juniper with its answer. */
    val failedLog get() = listOf(
        line("say", "deploying main"),
        line("out", "Already on 'main'"),
        line("out", "Your branch is up to date with 'origin/main'."),
        line("out", "building 14 pages… done"),
        line("out", "uploading public/ to the staging bucket (14 files, 1.2 MB) with cache-control max-age=300 on every page"),
        line("err", "Traceback (most recent call last):"),
        line("err", "  File \"deploy.py\", line 9, in deploy"),
        line("err", "    subprocess.run([\"make\", \"publish\"], check=True)"),
        line("err", "subprocess.CalledProcessError: Command '['make', 'publish']' returned non-zero exit status 2."),
        line("ask", "It failed: make publish exited with 2. Find out why.", error = true),
        line("answer", "The upload's key had expired. I renewed it and ran the task again: that run finished in 51 s."))

    /** The list. */
    fun list() = TasksState(live = false).apply {
        tasks.addAll(TaskFixtures.tasks)
        clock = Msg.Clock(zone = "UTC", offset = 0)
        loaded = true
    }

    /** Deploy site's page. */
    fun page() = list().apply {
        task = deploy
        runs.addAll(TaskFixtures.runs)
        page = "task"
    }

    /** The chats a run of Deploy site started on its failure (one done, one still working), as h1 lists them. */
    val started get() = listOf(
        Msg.Session(id = "c1", title = "Why the deploy failed", state = "idle", spawnedBy = "s1",
            taskRun = Msg.TaskRunRef(bot = "s1", task = "t1", run = "r3", name = "Deploy site")),
        Msg.Session(id = "c2", title = "Check the release links", state = "working", spawnedBy = "s1",
            taskRun = Msg.TaskRunRef(bot = "s1", task = "t1", run = "r3", name = "Deploy site")))

    /** That run, falling back to one-shot agents: Started, and what the done one said. */
    fun startedRun() = page().apply {
        run = TaskFixtures.runs[1].let { Msg.TaskRun.from(it.json().put("status", "done").put("offset", 0)
            .put("chats", org.json.JSONArray(listOf("c1", "c2")))) }
        log.addAll(listOf(line("say", "deploying main"), line("err", "upload failed: the key has expired"),
            Msg.TaskLogLine(at = now, kind = "agent", text = "The bucket's key expired yesterday. Renew it, then run the task again.",
                session = "c1")))
        bot = "Juniper"
        page = "run"
    }

    /** A run of Deploy site waiting for input: its script asked Juniper which bucket, the answer not in yet. */
    val waiting get() = run("r5", "t1", "Deploy site", "asking", "manual", now - 25 * 60, null, V)

    /** The list, Deploy site's last run waiting for input. */
    fun waitingList() = list().apply {
        tasks[0] = Msg.Task.from(deploy.json().put("last", waiting.json()))
    }

    /** Deploy site's page, its newest run waiting for input. */
    fun waitingPage() = waitingList().apply {
        task = tasks[0]
        runs.addAll(listOf(waiting) + TaskFixtures.runs)
        page = "task"
    }

    /** That run's page: what it said, and its ask, not answered yet. */
    fun waitingRun() = waitingPage().apply {
        run = Msg.TaskRun.from(waiting.json().put("offset", 0).put("asks", 1))
        log.addAll(listOf(line("say", "deploying main"), line("out", "building 14 pages… done"),
            line("ask", "Which bucket does site go to?\n\nAnswer with only a JSON object with these fields:\n" +
                "- bucket: one of \"staging\", \"production\"")))
        bot = "Juniper"
        page = "run"
    }

    /** The run that failed, its log. */
    fun failedRun() = page().apply {
        run = TaskFixtures.runs[1].let { Msg.TaskRun.from(it.json().put("handed", true).put("exit", 2)
            .put("error", "exited with 2").put("offset", 0)) }
        log.addAll(failedLog)
        bot = "Juniper"
        page = "run"
    }
}
